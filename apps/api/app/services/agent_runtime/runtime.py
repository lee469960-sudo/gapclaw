"""AgentRuntime — top-level facade for the single LLM-driven ReAct loop.

Composes:
- AgentContext (immutable invocation snapshot)
- AgentLoopState (mutable per-turn state)
- tool_parser (reply parsing/cleaning only — no gate decisions)
- agent_tools.execute_action (execution + security handled by the loop)
- ContextManager (layered context management)
- SystemPromptBuilder (message construction)

The engine owns protocol parsing, execution, and security interception only.
Completion, phase transitions, and budget are all decided by the LLM itself.

Usage:
    ctx = AgentContext.from_params(db=db, agent=agent, ...)
    runtime = AgentRuntime()
    final = await runtime.run(ctx)

    # Or use the drop-in replacement for run_react_loop:
    from app.services.agent_runtime import run_agent
    final = await run_agent(db, agent, session_id, user_message)
"""

from __future__ import annotations

import base64
import asyncio
import json
import logging
import re
import time
from typing import TYPE_CHECKING

from app.services.tool_parser import (
    LEAKED_TOOL_TOKEN_RE,
    PROTOCOL_MARKERS,
    strip_leaked_tool_tokens,
)
from app.services.agent_runtime.observability import NoProgressHintLogAggregator
from app.services.agent_runtime.utils import build_ads_view_catalog

if TYPE_CHECKING:
    from app.services.agent_runtime.context import AgentContext


_EXECUTION_DETAIL_LIMIT = 12_000

logger = logging.getLogger(__name__)

# The loop ends only on a strictly-formatted FINAL line (or cancel / LLM error /
# max_iters budget). There are no static "stall" thresholds that force-stop the run;
# convergence nudges are delivered as soft coach hints (see _run_modular).

# Consecutive *effective* completion-review FAILs (non-empty, non-boilerplate
# fix_list) before the loop accepts the candidate instead of spinning to budget
# exhaustion (D4). A successful real tool execution resets the count to 0.
_REFLECT_FAIL_CONVERGE = 3

# react-engine-v15 R1′: every N consecutive no-progress rounds, inject a template
# 「破局复盘」coach hint (soft, non-terminating). Never stops the loop — max_iters
# remains the only hard budget. The same N acts as the cooldown interval (fires at
# N, 2N, 3N …), mirroring the existing "every +N" hint cadence.
_NO_PROGRESS_HINT_EVERY = 5

# react-engine-v16 R1: completion-signal soft-conversion. A tool-free reply that
# reads as a positive completion declaration (e.g.「已完成/最终交付/无需再调用
# 工具」) counts toward a soft signal; after N consecutive such rounds an LLM
# confirms it, then the text is escalated to a FINAL candidate for the Verifier.
# Negative "cannot complete" phrasings and questions never trigger. Soft — no gate.
_COMPLETION_SIGNAL_CONFIRM_AT = 2
_COMPLETION_SIGNAL_DUPLICATE_FINAL_AT = 2
_CACHED_REFERENCE_HARD_STOP_AT = 3
_COMPLETION_DECL_RE = re.compile(
    r"(已完成|已完结|任务完成|任务已完结|已经完成|最终交付|已交付|交付完成"
    r"|确认完成|无需再调用工具|不再需要调用工具|所有子任务已完成|全部完成)"
)
_COMPLETION_NEG_RE = re.compile(
    r"(无法|不能)\s*(完成|继续|连接|交付)"
    r"|未完成|尚未完成|还没完成|没有完成|未能完成"
    r"|吗|？|\?|是否|请问"
)


def _looks_like_completion_declaration(text: str) -> bool:
    """Coarse positive-completion pre-filter (react-engine-v16 R1).

    True when ``text`` carries a positive completion keyword and no negative
    completion phrasings or question markers. This is only the first, cheapest
    stage — escalation still requires ``_COMPLETION_SIGNAL_CONFIRM_AT`` consecutive
    matches plus a dedicated LLM confirmation, and the Verifier stays in the loop.
    """
    return bool(_COMPLETION_DECL_RE.search(text)) and not bool(_COMPLETION_NEG_RE.search(text))


_BOILERPLATE_FIX_RE = re.compile(
    r"^(任务尚未完成|尚未完成|还未完成|没有完成|未完成|"
    r"再检查一下|请再检查|再核对一下|请再核对|任务未完成|还需努力)$"
)


def _subtasks_ready_for_final(subtasks: list | None) -> bool:
    """True when there are no subtasks, or every named subtask is done."""
    items = [
        s for s in (subtasks or [])
        if isinstance(s, dict) and str(s.get("text") or "").strip()
    ]
    if not items:
        return True
    return all(s.get("status") == "done" for s in items)


def _open_subtask_texts(subtasks: list | None) -> list[str]:
    return [
        str(s.get("text") or "").strip()
        for s in (subtasks or [])
        if isinstance(s, dict)
        and str(s.get("text") or "").strip()
        and s.get("status") != "done"
    ]


def _is_boilerplate_fix_item(text: str) -> bool:
    t = str(text or "").strip(" -·*").strip()
    return (not t) or bool(_BOILERPLATE_FIX_RE.fullmatch(t))


def _effective_fix_list(fix_list: list | None) -> list[str]:
    """Drop empty/boilerplate bullets. Empty result means the FAIL is ignored."""
    items = [str(x).strip() for x in (fix_list or []) if str(x).strip()]
    return [x for x in items if not _is_boilerplate_fix_item(x)]


def _parse_verifier_gap_cards(verdict: str) -> list[dict[str, str]]:
    """Extract complete, machine-checkable evidence gaps from a verifier reply."""
    text = (verdict or "").strip()
    if not text:
        return []
    blocks = re.split(r"(?=^\s*GAP(?:\s+\d+)?\s*[:：])", text, flags=re.IGNORECASE | re.MULTILINE)
    cards: list[dict[str, str]] = []
    aliases = {
        "requirement": "requirement", "需求": "requirement",
        "missing_evidence": "missing_evidence", "缺失证据": "missing_evidence",
        "action": "action", "动作": "action",
        "criterion": "criterion", "判定条件": "criterion",
    }
    for block in blocks:
        if not re.match(r"^\s*GAP(?:\s+\d+)?\s*[:：]", block, re.IGNORECASE):
            continue
        card: dict[str, str] = {}
        for line in block.splitlines()[1:]:
            key, sep, value = line.partition(":")
            if not sep:
                key, sep, value = line.partition("：")
            canonical = aliases.get(key.strip().lower())
            if canonical and value.strip():
                card[canonical] = value.strip().strip("`")
        if all(card.get(field) for field in ("requirement", "missing_evidence", "action", "criterion")):
            cards.append(card)
    return cards


def _gap_action_signature(action: str) -> str:
    return " ".join((action or "").strip().split()).lower()


def _is_high_risk_gap_action(action: str) -> bool:
    text = (action or "").strip()
    if re.match(r"^(READ|MCP|SEARCH)\s*:", text, re.IGNORECASE):
        return False
    if not re.match(r"^SHELL\s*:", text, re.IGNORECASE):
        return True
    cmd = text.split(":", 1)[1].strip()
    if re.search(r"(?:>|>>|\brm\b|\bmv\b|\bcp\b|\bcurl\b|\bwget\b|\bgit\s+push\b|\bdeploy\b)", cmd, re.IGNORECASE):
        return True
    return not bool(re.match(r"^(cat|ls|rg|grep|sed|head|tail|wc|find|echo)\b", cmd))


def _exhausted_gap_cards(state, cards: list[dict] | None) -> list[dict]:
    exhausted: list[dict] = []
    for card in cards or []:
        gap_id = "\x1f".join(str(card.get(k) or "").strip() for k in ("requirement", "missing_evidence", "criterion"))
        prior = (getattr(state, "verifier_gaps", {}) or {}).get(gap_id, {})
        if len(set(prior.get("signatures") or [])) >= 2:
            exhausted.append(card)
    return exhausted


def _validated_gap_cards(state, cards: list[dict] | None) -> list[dict]:
    """Keep only actionable gaps not already covered by persisted evidence."""
    valid: list[dict] = []
    cached_paths = {
        str(entry.get("path") or "")
        for entry in (getattr(state, "query_cache", {}) or {}).values()
        if isinstance(entry, dict)
    }
    for card in cards or []:
        action = str(card.get("action") or "").strip()
        if not re.match(r"^(READ|SHELL|MCP|SEARCH)\s*:", action, re.IGNORECASE):
            continue
        signature = _gap_action_signature(action)
        gap_id = "\x1f".join(str(card.get(k) or "").strip() for k in ("requirement", "missing_evidence", "criterion"))
        prior = (getattr(state, "verifier_gaps", {}) or {}).get(gap_id, {})
        if len(set(prior.get("signatures") or [])) >= 2:
            continue
        if signature in (prior.get("signatures") or []):
            continue
        read_path = action.split(":", 1)[1].strip() if action.upper().startswith("READ:") else ""
        if read_path and read_path in cached_paths:
            continue
        accepted = dict(card)
        accepted["gap_id"] = gap_id
        accepted["signature"] = signature
        valid.append(accepted)
    return valid


def _persist_code_profile_event(db, run_id: str, payload: dict) -> None:
    """Persist a redacted Code event so history survives WebSocket reconnects."""
    if db is None or not run_id:
        return
    try:
        from app.models import CodeAgentRun
        from app.services.code_agent.output_security import redact_code_output

        run = db.query(CodeAgentRun).filter(CodeAgentRun.id == run_id).first()
        if not run:
            return
        try:
            facts = json.loads(getattr(run, "runner_facts", "") or "{}")
        except (TypeError, json.JSONDecodeError):
            facts = {}
        if not isinstance(facts, dict):
            facts = {}
        events = facts.get("code_profile_events")
        if not isinstance(events, list):
            events = []
        safe_payload = {
            str(key): redact_code_output(value).text if isinstance(value, str) else value
            for key, value in payload.items()
        }
        try:
            previous_sequence = int(events[-1].get("sequence", -1)) if events and isinstance(events[-1], dict) else -1
        except (TypeError, ValueError):
            previous_sequence = len(events) - 1
        safe_payload["sequence"] = previous_sequence + 1
        events.append(safe_payload)
        facts["code_profile_events"] = events[-500:]
        run.runner_facts = json.dumps(facts, ensure_ascii=False, sort_keys=True)
        db.commit()
    except Exception:
        # Event persistence must not make the coding run fail; the live hub
        # remains the best-effort path when the database is unavailable.
        logger.exception("code profile event persistence failed run=%s", run_id)


_CODE_PHASE_LABELS = {
    "prepare": "准备运行环境",
    "verify_baseline": "捕获验证基线",
    "runtime_started": "启动 Claude Code Runtime",
    "runtime_result": "Claude Code Runtime 结果",
    "skill_loaded": "加载 Claude Code Skill",
    "mcp_loaded": "加载 Claude Code MCP",
    "tool_call": "Claude Code 工具调用",
    "file_changed": "Claude Code 文件变化",
    "test_run": "Claude Code 测试执行",
    "verifier_failed_retrying": "Verifier 失败，继续 Claude Code 修复",
    "verifier_passed": "Verifier 已通过",
    "artifact_sealed": "封存工件已生成",
    "verify": "执行验证",
    "seal": "封装可采用补丁",
    "cleanup": "清理 Sandbox",
    "terminate": "结束 Code Run",
}


def _code_profile_steps_for_message(run) -> list[dict]:
    """Convert persisted Code profile events into compact chat execution steps."""
    if not run:
        return []
    try:
        facts = json.loads(getattr(run, "runner_facts", "") or "{}")
    except (TypeError, json.JSONDecodeError):
        return []
    events = facts.get("code_profile_events") if isinstance(facts, dict) else []
    if not isinstance(events, list):
        return []
    steps: list[dict] = []
    open_indexes: dict[str, int] = {}
    terminal = {"completed", "done", "passed", "success"}
    for event in events:
        if not isinstance(event, dict):
            continue
        phase = str(event.get("phase") or "profile")
        raw_status = str(event.get("status") or "running").lower()
        status = "error" if raw_status in {"failed", "error"} else (
            "done" if raw_status in terminal or (phase in {"prepare", "runtime_started"} and raw_status == "started")
            else "running"
        )
        label = _CODE_PHASE_LABELS.get(phase, "CodeAgent 运行阶段")
        step = {
            "type": "info",
            "action": f"code_{phase}",
            "title": f"{label} · {event.get('status') or 'running'}",
            "status": status,
        }
        detail = (
            event.get("reason")
            or event.get("summary")
            or event.get("skill_name")
            or event.get("mcp_name")
            or event.get("command")
            or event.get("path")
            or event.get("artifact_id")
            or ""
        )
        if isinstance(detail, str) and detail.strip():
            step["content"] = detail[:400]
        snippet = event.get("snippet") or event.get("output") or ""
        if isinstance(snippet, str) and snippet.strip():
            # Keep the persisted chat step bounded; the complete redacted
            # event remains available through the Code run event endpoint.
            step["snippet"] = snippet[:3200]
        current = open_indexes.get(phase)
        if current is not None and steps[current].get("status") not in {"done", "error"}:
            steps[current].update(step)
        else:
            open_indexes[phase] = len(steps)
            steps.append(step)
    return steps


class AgentRuntime:
    """Top-level agent runtime orchestrating the LLM-driven ReAct loop.

    Routes tool-free chat to ConversationalHandler, everything else to a single
    loop where the LLM decides continue / switch / finish each round.
    """

    # ---- Public interface ----

    async def run(self, ctx: AgentContext) -> str:
        """Execute the LLM-driven ReAct loop for a given invocation context."""
        from app.services.agent_runtime.hub import _running

        key = ctx.chat_key
        if _running.get(key):
            _running[key] = False
        _running[key] = True

        # Persist user turn immediately so refresh mid-run still shows the question
        self._save_user_message(ctx)
        await self._publish_inbound_events(ctx)
        await self._publish_code_profile_event(ctx, "prepare", "started")

        terminal_status = "completed"
        try:
            if self._is_conversational(ctx):
                logger.info(
                    "agent_run conversational agent=%s session=%s msg_len=%d",
                    ctx.agent.id, ctx.session_id, len(ctx.user_message or ""),
                )
                result = await self._run_conversational(ctx)
                self._save_assistant_message(ctx, result, steps=[{
                    "type": "info",
                    "action": "conversational_reply",
                    "title": "对话回复",
                    "status": "done",
                }])
                return result

            logger.info(
                "agent_run modular agent=%s session=%s msg_len=%d mcp=%d skills=%d",
                ctx.agent.id, ctx.session_id, len(ctx.user_message or ""),
                len(ctx.mcp_ids), len(ctx.skill_ids),
            )
            result, steps, saved_paths, files_written, context_pct = await self._run_modular(ctx)
            self._save_assistant_message(
                ctx, result, steps=steps, saved_paths=saved_paths,
                context_available_percent=context_pct,
            )
            await self._publish_modular_done(
                ctx, result, files_written=files_written, saved_paths=saved_paths,
            )
            return result
        except Exception:
            terminal_status = "failed"
            raise
        finally:
            _running[key] = False
            await self._publish_code_profile_event(ctx, "terminate", terminal_status)

    @staticmethod
    async def _publish_code_profile_event(
        ctx: AgentContext,
        phase: str,
        status: str,
        reason: str = "",
        extra: dict | None = None,
    ) -> None:
        """Append a versioned Code payload without changing the shared envelope."""
        code_execution = getattr(ctx, "code_execution", None)
        if not code_execution:
            return
        from app.services.agent_runtime.hub import hub

        payload = {
            "version": 1,
            "profile": "code",
            "phase": phase,
            "status": status,
            "run_id": code_execution.run_id,
            "manifest_version": code_execution.manifest_version,
        }
        if reason:
            payload["reason"] = reason
        if extra:
            payload.update(extra)
        _persist_code_profile_event(ctx.db, code_execution.run_id, payload)
        try:
            await hub.publish(ctx.chat_key, {
                "type": "profile",
                "agent_id": ctx.agent.id,
                "session_id": ctx.session_id,
                "profile": payload,
            })
        except Exception:
            logger.exception("code profile event publish failed phase=%s", phase)

    @staticmethod
    def _save_user_message(ctx: AgentContext) -> None:
        """Persist the user message to ChatMessage."""
        import json as _json
        from app.models import ChatMessage
        from app.security import now_str

        user_meta = dict(ctx.message_meta or {})
        ctx.db.add(ChatMessage(
            agent_id=ctx.agent.id,
            session_id=ctx.session_id,
            role="user",
            content=ctx.user_message or "",
            meta=_json.dumps(user_meta, ensure_ascii=False) if user_meta else "{}",
            created_at=now_str(),
        ))
        try:
            ctx.db.commit()
        except Exception:
            logger.exception(
                "save_user_message failed agent=%s session=%s",
                getattr(ctx.agent, "id", ""),
                ctx.session_id,
            )

    @staticmethod
    async def _publish_inbound_events(ctx: AgentContext) -> None:
        """Push an inbound IM message to (a) its session stream and (b) the agent inbox.

        Web-submitted messages are excluded: the UI already surfaces them via
        optimistic append + step/done events.
        """
        from app.services.agent_runtime.hub import hub, inbox_key

        mm = ctx.message_meta or {}
        source = (mm.get("source") or "")
        if not source.startswith("im:"):
            return
        sender = (
            mm.get("sender_username")
            or mm.get("sender_display_name")
            or mm.get("user_id")
            or ""
        )
        base = {
            "session_id": ctx.session_id,
            "content": ctx.user_message or "",
            "chat_id": (mm.get("chat_id") or "").strip(),
            "source": source,
            "sender": sender,
        }
        try:
            await hub.publish(ctx.chat_key, {"type": "user_message", **base})
        except Exception:
            logger.exception("publish user_message failed")
        try:
            await hub.publish(
                inbox_key(ctx.agent.id),
                {"type": "inbound", "agent_id": ctx.agent.id, **base},
            )
        except Exception:
            logger.exception("publish inbound failed")

    @staticmethod
    def _slim_steps_for_meta(steps: list[dict] | None) -> list[dict]:
        """Keep bounded, user-visible execution details in ChatMessage.meta."""
        out: list[dict] = []
        for s in steps or []:
            if not isinstance(s, dict) or s.get("hidden"):
                continue
            item: dict = {
                "type": s.get("type"),
                "action": s.get("action"),
                "title": s.get("title"),
                "status": s.get("status"),
            }
            if s.get("iteration") is not None:
                item["iteration"] = s.get("iteration")
            if s.get("type") == "model_route" and isinstance(s.get("detail"), dict):
                detail = s["detail"]
                item["collapsed"] = bool(s.get("collapsed", True))
                item["detail"] = {
                    "version": int(detail.get("version") or 1),
                    "kind": str(detail.get("kind") or "")[:64],
                    "decision_id": str(detail.get("decision_id") or "")[:64],
                    "policy_id": str(detail.get("policy_id") or "")[:64],
                    "policy_version": int(detail.get("policy_version") or 0),
                    "role": str(detail.get("role") or "")[:64],
                    "frozen_model_id": str(detail.get("frozen_model_id") or "")[:64],
                    "candidate_ids": [str(v)[:64] for v in (detail.get("candidate_ids") or [])[:20]],
                    "exclusions": [
                        {"model_id": str(v.get("model_id") or "")[:64], "reason": str(v.get("reason") or "")[:80]}
                        for v in (detail.get("exclusions") or [])[:40] if isinstance(v, dict)
                    ],
                    "failure": str(detail.get("failure") or "")[:80],
                    "duration_ms": max(0, int(detail.get("duration_ms") or 0)),
                }
            preview = s.get("preview")
            if isinstance(preview, str) and preview.strip():
                cleaned_preview = AgentRuntime._sanitize_step_text(preview)
                if cleaned_preview:
                    item["preview"] = cleaned_preview[:300]
            content = s.get("content")
            if isinstance(content, str) and content.strip():
                cleaned_content = AgentRuntime._sanitize_step_text(content)
                if cleaned_content:
                    # These are user-visible audit details.  Persist enough of a
                    # successful tool result to make the history accordion useful;
                    # the LLM context remains independently clipped elsewhere.
                    item["content"] = cleaned_content[:_EXECUTION_DETAIL_LIMIT]
            snippet = s.get("snippet")
            if str(s.get("action") or "").startswith("code_") and isinstance(snippet, str) and snippet.strip():
                cleaned_snippet = AgentRuntime._sanitize_step_text(snippet)
                if cleaned_snippet:
                    item["snippet"] = cleaned_snippet[:1200]
            out.append(item)
        return out

    @staticmethod
    def _ensure_visible_run_steps(steps: list[dict] | None) -> list[dict]:
        """Guarantee ≥1 visible step for the UI exec-card."""
        visible = list(steps or [])
        if visible:
            return visible
        return [{
            "type": "info",
            "action": "no_tools",
            "title": "本轮未调用工具",
            "status": "done",
        }]

    @staticmethod
    def _save_assistant_message(
        ctx: AgentContext,
        reply: str,
        *,
        steps: list[dict] | None = None,
        saved_paths: list[str] | None = None,
        context_available_percent: int | None = None,
    ) -> None:
        """Persist the assistant reply + execution steps to ChatMessage."""
        import json as _json
        from app.models import ChatMessage
        from app.security import now_str

        content = (reply or "").strip()
        if not content:
            n_steps = len(steps or [])
            content = (
                f"（本轮执行了 {n_steps} 个步骤，但未输出文字结论；详见执行过程）"
                if n_steps
                else "（本轮未产生文字回复；详见执行过程）"
            )
        visible = AgentRuntime._ensure_visible_run_steps(
            AgentRuntime._slim_steps_for_meta(steps),
        )
        meta = {
            "steps": visible,
            "step_count": max(len(visible), 1),
            "saved_paths": list(saved_paths or [])[:20],
        }
        if context_available_percent is not None:
            meta["context_available_percent"] = context_available_percent
        user_meta = dict(ctx.message_meta or {})
        for k in (
            "source",
            "channel_id",
            "chat_id",
            "chat_type",
            "user_id",
            "sender_username",
            "sender_display_name",
        ):
            if user_meta.get(k):
                meta[k] = user_meta[k]

        ctx.db.add(ChatMessage(
            agent_id=ctx.agent.id,
            session_id=ctx.session_id,
            role="assistant",
            content=content,
            meta=_json.dumps(meta, ensure_ascii=False),
            created_at=now_str(),
        ))
        try:
            ctx.db.commit()
        except Exception:
            pass

    # Non-resource tool actions that force the modular loop even without any
    # bound resource (shell-only agents, file tools, etc.). Resource-bound actions
    # (mcp_tool_call/rag_query/httpmcp_call/skill_*) don't count here: their
    # bindings are checked separately below.
    _TOOL_ACTIONS = {
        "shell", "file_read", "file_write", "file_search_replace", "file_search", "recall",
        "code_read", "code_search", "code_edit", "code_test",
        "code_shell", "code_git",
    }

    @staticmethod
    def _is_conversational(ctx: AgentContext) -> bool:
        """True only for a tool-free chat: no bound resource AND no tool action."""
        if ctx.mcp_ids or ctx.rag_ids or ctx.skill_ids or ctx.httpmcp_ids:
            return False
        return not (set(ctx.allowed_actions) & AgentRuntime._TOOL_ACTIONS)

    @staticmethod
    def _llm_step_preview(reply: str) -> str:
        """Short preview so consecutive LLM steps are not collapsed by the UI."""
        text = AgentRuntime._sanitize_step_text(reply)
        if not text:
            return "模型返回空正文/不可执行工具调用"
        text = " ".join(text.split())
        return text[:200] if text else "(tool/empty)"

    @staticmethod
    def _native_tool_step_preview(tool_steps: list) -> str:
        names = [getattr(s, "action", "") for s in tool_steps or [] if getattr(s, "action", "")]
        if not names:
            return "模型返回空正文/不可执行工具调用"
        shown = ", ".join(f"[{n}]" for n in names[:6])
        suffix = f" …共 {len(names)} 个" if len(names) > 6 else ""
        return f"工具调用: {shown}{suffix}"

    @staticmethod
    def _sanitize_step_text(text: str) -> str:
        """Strip protocol/meta reasoning so execution-step text stays user-safe."""
        from app.services.tool_parser import (
            clean_display_text,
            clean_final_answer,
            extract_final_payload,
            is_final_reply,
        )

        raw = str(text or "").strip()
        if not raw:
            return ""
        if is_final_reply(raw):
            cleaned = clean_final_answer(raw)
        else:
            cleaned = clean_display_text(raw)
            cleaned = re.sub(
                r"(?im)^\s*(?:actually|wait|looking at|let me|so i need|"
                r"the previous turn|i think|i notice)\b.*$",
                "",
                cleaned,
            )
            if is_final_reply(cleaned):
                cleaned = clean_final_answer(extract_final_payload(cleaned))
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned

    @staticmethod
    async def _append_step(ctx: AgentContext, state, step: dict) -> None:
        """Append to run_steps and publish WS (awaited — avoids done race)."""
        from app.services.agent_runtime.hub import hub

        state.run_steps.append(step)
        idx = len(state.run_steps) - 1
        try:
            await hub.publish(ctx.chat_key, {
                "type": "step",
                "op": "append",
                "index": idx,
                "step": step,
            })
        except Exception:
            logger.exception("append_step publish failed")

    @staticmethod
    async def _patch_last_step(ctx: AgentContext, state, **updates) -> None:
        from app.services.agent_runtime.hub import hub

        if not state.run_steps:
            return
        idx = len(state.run_steps) - 1
        state.run_steps[idx].update(updates)
        try:
            await hub.publish(ctx.chat_key, {
                "type": "step",
                "op": "patch",
                "index": idx,
                "step": updates,
            })
        except Exception:
            logger.exception("patch_last_step publish failed")

    @staticmethod
    async def _publish_modular_done(
        ctx: AgentContext,
        result: str,
        files_written: int = 0,
        saved_paths: list[str] | None = None,
    ) -> None:
        """Publish final result to WebSocket hub."""
        from app.services.agent_runtime.hub import hub
        key = ctx.chat_key
        try:
            await hub.publish(key, {
                "type": "done",
                "content": (result or "")[:500],
                "content_truncated": len(result or "") > 500,
                "workplace_changed": files_written > 0 or bool(saved_paths),
            })
        except Exception:
            pass

    @staticmethod
    async def _append_tool_step(
        ctx: AgentContext,
        state,
        *,
        iteration: int,
        action: str,
        title: str,
        status: str = "done",
        content: str = "",
    ) -> None:
        """Record a tool step on the shared run_steps bus."""
        step = {
            "type": "tool",
            "action": action,
            "title": title,
            "status": status,
            "iteration": iteration,
        }
        if content:
            step["content"] = content[:_EXECUTION_DETAIL_LIMIT]
        await AgentRuntime._append_step(ctx, state, step)

    async def _run_conversational(self, ctx: AgentContext) -> str:
        """Single-turn conversational path — no tools, no ReAct loop."""
        from app.services.agent_runtime.conversational import ConversationalHandler
        from app.services.agent_runtime.hub import _running, hub
        from app.services.agent_runtime.system_prompt import SystemPromptBuilder
        from app.services.llm_client import chat_completion
        from app.services.tool_parser import clean_final_answer
        from app.models import ChatMessage

        key = ctx.chat_key
        effective = ctx.user_message

        history = (
            ctx.db.query(ChatMessage)
            .filter(
                ChatMessage.agent_id == ctx.agent.id,
                ChatMessage.session_id == ctx.session_id,
            )
            .order_by(ChatMessage.id.asc())
            .all()
        )
        messages = ConversationalHandler.build_messages(ctx.agent, history, effective)
        if (ctx.note_content or "").strip():
            messages.insert(1, {
                "role": "system",
                "content": (
                    "【会话备注·约束】以下备注用于补充默认口径，不是本轮用户消息；"
                    "用户显式要求优先：\n" + ctx.note_content.strip()[:3000]
                ),
            })

        final = ""
        try:
            try:
                if not ctx.llm:
                    raise RuntimeError("未配置 LLM")
                reply = await chat_completion(
                    ctx.llm, messages,
                    max_tokens=1024,
                    db=ctx.db,
                    timeout=getattr(ctx.agent, 'llm_timeout', None) or 60,
                )
                final = clean_final_answer(reply or "")
                if not final.strip():
                    raise RuntimeError("empty conversational reply")
            except Exception:
                logger.exception("Conversational LLM failed, using light fallback")
                final = SystemPromptBuilder.build_light_agent_reply(
                    ctx.agent, ctx.user_message,
                )

            try:
                await hub.publish(key, {
                    "type": "step",
                    "op": "append",
                    "index": 0,
                    "step": {
                        "type": "info",
                        "action": "conversational_reply",
                        "title": "对话回复",
                        "status": "done",
                        "content": (final or "")[:500],
                    },
                })
            except Exception:
                pass

            try:
                await hub.publish(key, {
                    "type": "done",
                    "content": (final or "")[:500],
                    "workplace_changed": False,
                })
            except Exception:
                pass
        finally:
            _running[key] = False

        return final

    async def _reflect_final(self, ctx: AgentContext, state, candidate: str):
        """Soft goal-anchored Verifier before accepting a FINAL.

        Reuses the agent's own LLM as a pure judge (no tools) to check whether
        the candidate final reply satisfies the task goal + long-term rules.
        Returns ``None`` when OK, else ``{"missing": str, "fix_list": [str], "revised_plan": str}``.
        The loop treats an empty/boilerplate fix_list as PASS and never applies
        ``revised_plan``. No counters, no hard gate — max_iters remains the bound.
        """
        from app.services.agent_runtime.hub import _running
        from app.services.llm_client import chat_completion

        goal = (getattr(state, "goal", "") or (ctx.user_message or "")).strip()
        memory = str(getattr(ctx.agent, "memory", "") or "").strip()
        saved = "\n".join(f"- {p}" for p in (state.saved_paths or [])) or "（无）"
        deliverable_evidence = _deliverable_evidence(
            state.saved_paths, getattr(ctx, "sandbox", None)
        )
        subtask_block = _render_subtask_list(state.subtasks) or "（无）"
        recent_progress = "\n".join(f"- {ln}" for ln in (state.progress_lines or [])[-8:]) or "（无）"
        prompt = (
            "你是任务完成度复核器。先把「任务目标」逐条拆成核对项（列/字段/口径/补充说明），"
            "再逐项判断「候选最终回复」是否满足，每项判 PASS/FAIL。\n"
            "对数据准确性任务，字段口径、数字、映射关系必须精确核对；只有排版、措辞可豁免，"
            "不得以「小瑕疵」为由放宽字段/数字/映射的核对。\n"
            f"任务目标：{goal[:1500] or '（无）'}\n"
            f"长期规则（必须遵守）：{memory[:1500] or '（无）'}\n"
            f"已保存文件：{saved}\n"
            f"交付物内容摘要（前 {_REFLECT_DELIVERABLE_LINES} 行，供核对数字/SQL/字段口径）：\n{deliverable_evidence[:1500]}\n"
            f"子任务清单（[x]=已完成）：\n{subtask_block[:1500]}\n"
            f"最近进度：\n{recent_progress[:1500]}\n"
            f"候选最终回复：\n{candidate[:3000]}\n\n"
            "只输出：若全部核对项 PASS，输出一行 `PASS`；否则输出 `FAIL: <原因>`，并为每个"
            "真正阻塞项输出以下四行（缺任何一行都视为非阻塞备注）：\n"
            "GAP:\nrequirement: <原始用户目标中的可验证需求>\n"
            "missing_evidence: <当前尚未拥有的证据>\n"
            "action: <一条尚未执行的具体工具协议行>\n"
            "criterion: <该动作结果如何判定需求满足>\n"
            "不得要求再检查/再推理；不得重复已完成动作；不要输出 PLAN 或其它内容。"
        )
        try:
            verdict = await chat_completion(
                ctx.llm,
                [{"role": "user", "content": prompt}],
                max_tokens=512,
                db=ctx.db,
                timeout=getattr(ctx.agent, "llm_timeout", None) or 120,
                cancel_check=lambda: not _running.get(ctx.chat_key, False),
            )
        except Exception:
            logger.warning("reflect_final failed agent=%s", ctx.agent.id, exc_info=True)
            return None  # reflection unavailable → don't block FINAL
        verdict = (verdict or "").strip()
        if re.match(r"^\s*PASS\b", verdict, re.IGNORECASE):
            return None
        fail_m = re.match(r"^\s*FAIL\s*[:：]?\s*(.*)", verdict, re.IGNORECASE | re.DOTALL)
        body = fail_m.group(1) if fail_m else verdict
        parts = re.split(r"修复清单\s*[:：]?\s*", body, maxsplit=1)
        missing = parts[0].strip() or "任务尚未完成"
        fix_list: list[str] = []
        revised_plan = ""
        if len(parts) > 1:
            rest = parts[1]
            rp_parts = re.split(r"修订\s*PLAN\s*[:：]?\s*", rest, maxsplit=1)
            fix_list = [
                ln.strip(" -·*").strip()
                for ln in rp_parts[0].splitlines()
                if ln.strip(" -·*").strip()
            ]
            if len(rp_parts) > 1:
                revised_plan = rp_parts[1].strip()
        return {
            "missing": missing,
            "fix_list": fix_list,
            "revised_plan": revised_plan,
            "gaps": _parse_verifier_gap_cards(verdict),
            "raw_verdict": verdict,
        }

    async def _confirm_completion_signal(self, ctx: AgentContext, text: str) -> bool:
        """One LLM check: is this text a task-completion declaration? (react-engine-v16 R1)

        Soft escalation guard — only runs after ``_COMPLETION_SIGNAL_CONFIRM_AT``
        consecutive coarse matches. On any LLM failure returns False (do not
        escalate), so the loop keeps its budget bound and never hard-stops here.
        """
        from app.services.agent_runtime.hub import _running
        from app.services.llm_client import chat_completion

        prompt = (
            "判断以下文本是否表达了「任务已全部完成、无需再调用工具」的完成声明。"
            "只输出一行 YES 或 NO，不要输出其它内容。\n"
            f"文本：\n{text[:2000]}"
        )
        try:
            verdict = await chat_completion(
                ctx.llm,
                [{"role": "user", "content": prompt}],
                max_tokens=8,
                db=ctx.db,
                timeout=getattr(ctx.agent, "llm_timeout", None) or 120,
                cancel_check=lambda: not _running.get(ctx.chat_key, False),
            )
        except Exception:
            logger.warning("confirm_completion_signal failed agent=%s", ctx.agent.id, exc_info=True)
            return False
        return bool(re.match(r"^\s*(YES|是|对|yes)\b", (verdict or "").strip(), re.IGNORECASE))

    async def _distill_final(self, ctx: AgentContext, state, reason: str) -> str:
        """One-shot LLM distillation when the loop exits without a FINAL.

        Summarizes accumulated progress / deliverables / unfinished subtasks into
        an honest best-effort final (explicitly marking gaps), so a budget-exhausted
        run still surfaces a useful conclusion instead of a bare "not done".
        Falls back to the template on LLM error. Not a hard gate.
        """
        from app.services.agent_runtime.hub import _running
        from app.services.llm_client import chat_completion

        goal = (getattr(state, "goal", "") or (ctx.user_message or "")).strip()
        subtask_lines = _render_subtask_list(state.subtasks) or "（无子任务清单）"
        progress = [ln for ln in state.progress_lines if ln.strip()][-12:]
        progress_txt = "\n".join(f"- {ln}" for ln in progress) if progress else "（无）"
        saved = "\n".join(f"- {p}" for p in (state.saved_paths or [])[-8:]) or "（无）"
        prompt = (
            "你是任务收尾总结器。任务未能在预算内完成，请基于已取得的进展，产出一份诚实、"
            "对用户有用的最终回复。\n"
            f"任务目标：{goal[:1500] or '（无）'}\n"
            f"子任务状态：\n{subtask_lines}\n"
            f"已取得进展：\n{progress_txt}\n"
            f"已产生文件：\n{saved}\n"
            f"结束原因：{reason}\n\n"
            "要求：用 Markdown 排版；先说已完成的部分与已交付文件（路径用反引号），"
            "再明确说明还缺什么、如何继续；不要编造未完成的结果，不要输出工具协议行。"
        )
        try:
            final = await chat_completion(
                ctx.llm,
                [{"role": "user", "content": prompt}],
                max_tokens=1024,
                db=ctx.db,
                timeout=getattr(ctx.agent, "llm_timeout", None) or 120,
                cancel_check=lambda: not _running.get(ctx.chat_key, False),
            )
        except Exception:
            logger.warning("distill_final failed agent=%s", ctx.agent.id, exc_info=True)
            return _forced_stop_reply(state, reason)
        final = (final or "").strip()
        return final or _forced_stop_reply(state, reason)

    # ---- LLM-driven ReAct loop ----

    async def _run_modular(self, ctx: AgentContext) -> tuple[str, list[dict], list[str], int, int]:
        """Single LLM-driven loop: parse → security-gate → execute → observe.

        The LLM decides when to continue, switch tools, or finish. The engine
        only parses protocol lines, blocks disallowed actions, and feeds results
        back into context. Returns (final_reply, run_steps, saved_paths,
        files_written, context_available_percent).
        """
        from app.services.agent_runtime.context_manager import ContextManager
        from app.services.agent_runtime.hub import ChatStopped, _running
        from app.services.agent_runtime.loop_state import AgentLoopState
        from app.services.agent_runtime.system_prompt import SystemPromptBuilder
        from app.services.agent_tools import execute_action, dedup_descriptor, read_cache_key
        from app.services.llm_client import chat_completion, tool_steps_from_tool_calls
        from app.services.llm_client import LLMProviderThrottled, LLMTransportError
        from app.services.mcp_client import McpSessionManager
        from app.services.agent_runtime.mcp_routing import (
            build_mcp_route_candidates,
            route_mcp_candidates,
        )
        from app.services.tool_parser import extract_tool_steps, _strip_reasoning_blocks

        state = AgentLoopState()
        state.model_route_decision_id = getattr(ctx, "model_route_decision_id", "")
        state.frozen_llm_id = getattr(ctx.llm, "id", "") or ""
        state.goal = (ctx.user_message or "").strip()
        no_progress_logs = NoProgressHintLogAggregator(logger)
        resume_state = _load_run_state(ctx)
        if resume_state is not None:
            state = resume_state
            if not state.goal:
                state.goal = (ctx.user_message or "").strip()
            state.model_route_decision_id = getattr(ctx, "model_route_decision_id", "")
            state.frozen_llm_id = getattr(ctx.llm, "id", "") or ""
        cm = ContextManager()
        sp_builder = SystemPromptBuilder()
        # Reuse the resumed run's output dir so old mcp_result_*.json stay addressable;
        # generate a fresh one for a brand-new run.
        run_ts = state.run_ts or str(int(time.time() * 1000))
        state.run_ts = run_ts
        pre_existing_deliverables: set[str] = set()

        def _ret(final: str) -> tuple[str, list[dict], list[str], int, int]:
            _capture_deliverable_paths(ctx, state, pre_existing_deliverables)
            return final, list(state.run_steps), list(state.saved_paths), state.files_written, _context_available_percent(ctx, cm)

        def _apply_no_progress_hint(made_progress: bool, round_no: int) -> None:
            """react-engine-v15 R1′: advance/reset the no-progress streak and, every
            ``_NO_PROGRESS_HINT_EVERY`` consecutive no-progress rounds, inject a
            template「破局复盘」coach hint. Soft and non-terminating — never stops
            the loop; max_iters remains the only hard budget.

            Progress resets the streak; a no-progress round increments it. The
            cooldown falls out of the same counter (fires at N, 2N, 3N …), matching
            the existing "every +N" hint cadence so the hint never floods.
            """
            if made_progress:
                state.no_progress_streak = 0
                no_progress_logs.reset(ctx.agent.id)
                return
            state.no_progress_streak += 1
            streak = state.no_progress_streak
            if streak < _NO_PROGRESS_HINT_EVERY or (streak - _NO_PROGRESS_HINT_EVERY) % _NO_PROGRESS_HINT_EVERY != 0:
                return
            progress = [ln for ln in state.progress_lines if ln.strip()][-8:]
            done_txt = "\n".join(f"- {ln}" for ln in progress) if progress else "（暂无）"
            pending = [
                s["text"] for s in state.subtasks
                if s.get("status") != "done" and (s.get("text") or "").strip()
            ]
            pending_txt = (
                "\n".join(f"- {t}" for t in pending)
                if pending
                else "（无待办子任务，请直接输出 FINAL）"
            )
            hint = (
                f"【破局复盘】你已连续 {streak} 轮无进展（无工具成功 / 无文件写入 / "
                "无进度新增 / 无子任务推进），请停下复盘并二选一：\n"
                f"已完成：\n{done_txt}\n"
                f"仍缺：\n{pending_txt}\n"
                "请二选一：1) 调用工具推进（无依赖可同轮多个）；"
                "2) 输出一行 `FINAL: <当前结论>` 收尾。"
            )
            no_progress_logs.log_hint(ctx.agent.id, round_no, streak)
            cm.add_coach_hint(hint)

        if not ctx.llm:
            raise RuntimeError("未配置 LLM")

        llm_timeout = getattr(ctx.agent, 'llm_timeout', None) or 120
        max_iters = max(1, int(getattr(ctx.agent, 'max_iterations', None) or 50))
        soft_circuit = max(1, int(getattr(ctx.agent, 'mcp_soft_circuit', None) or 5))
        tool_result_clip = max(1, int(getattr(ctx.agent, 'tool_result_clip', None) or 6000))

        # Native function-calling (Q4=A): declare the meta-tool set only for
        # OpenAI-compatible providers; the text protocol remains the fallback.
        provider = (getattr(ctx.llm, "provider", "") or "").lower()
        tool_schemas = (
            sp_builder.build_tool_schemas(ctx.allowed_actions)
            if provider in ("", "openai", "minimax", "deepseek")
            else None
        )

        # ---- System prompt ----
        system_prompt = sp_builder.build_system_base(
            ctx.agent, summary_text=_load_session_summary(ctx),
        )
        if (ctx.note_content or "").strip():
            system_prompt += (
                "\n\n【会话备注·约束】以下备注用于补充默认口径、时区和文件规则，"
                "不是本轮用户任务；用户显式要求优先：\n"
                + ctx.note_content.strip()[:3000]
            )
        skill_snap = sp_builder.build_skill_snapshot(ctx.skill_mds) if ctx.skill_mds else None
        if skill_snap:
            system_prompt += f"\n\n{skill_snap}"
        cm.set_base(system_prompt=system_prompt)
        candidates = build_mcp_route_candidates(
            ctx.db, ctx.mcp_ids, ctx.allowed_actions
        )
        route_decision = await route_mcp_candidates(
            llm=ctx.llm,
            db=ctx.db,
            user_message=state.goal or ctx.user_message,
            candidates=candidates,
            timeout=llm_timeout,
        )
        state.selected_mcp_ids = route_decision.selected_mcp_ids
        view_catalog = build_ads_view_catalog(state.selected_mcp_ids)
        if state.resumed:
            # Resumed run: inject the rendered subtask list + raw plan (carries view_map)
            # so the model sees [x]/[ ] progress and the original bindings again.
            resumed_plan = _render_subtask_list(state.subtasks)
            if state.plan_text:
                resumed_plan = f"{resumed_plan}\n\n{state.plan_text}".strip()
            cm.set_task_context(goal=state.goal or ctx.user_message, plan=resumed_plan, view_catalog=view_catalog, checklist=_build_completed_checklist(state))
        else:
            cm.set_task_context(goal=state.goal or ctx.user_message, view_catalog=view_catalog, checklist=_build_completed_checklist(state))

        # ---- Tools catalog ----
        try:
            tools_block = await SystemPromptBuilder.build_tools_desc(
                db=ctx.db,
                agent=ctx.agent,
                allowed=ctx.allowed_actions,
                skill_ids=ctx.skill_ids,
                mcp_ids=state.selected_mcp_ids,
                rag_ids=ctx.rag_ids,
                httpmcp_ids=ctx.httpmcp_ids,
                save_dir=ctx.save_dir,
                im_source=ctx.im_source,
            )
            cm.set_tools_catalog(tools_block)
            _record_mcp_route_event(
                state, user_message=state.goal or ctx.user_message, candidates=candidates,
                decision=route_decision, trigger="initial", supplement_index=0,
                mcp_load_results=getattr(tools_block, "mcp_load_results", []),
            )
        except Exception:
            logger.warning("Failed to build tools catalog, using minimal block", exc_info=True)
            cm.set_tools_catalog(SystemPromptBuilder.build_minimal_tools_desc(
                save_dir=ctx.save_dir,
                allowed_actions=ctx.allowed_actions,
                mcp_ids=state.selected_mcp_ids,
                db=ctx.db,
            ))
            _record_mcp_route_event(
                state, user_message=state.goal or ctx.user_message, candidates=candidates,
                decision=route_decision, trigger="initial", supplement_index=0,
                mcp_load_results=[], load_failure="catalog_unavailable",
            )

        # ---- Establish dynamic system layers BEFORE any user/assistant message,
        # so coach_hint + progress_block stay in the leading system block and are
        # updated in place each iteration (never interleaved into the chat).
        if state.resumed:
            # Backfill prior materialized MCP results + saved files so the model
            # sees what's already on disk and skips redoing it.
            known = set(state.progress_lines)
            for mr in state.mcp_results:
                path = mr.get("path") if isinstance(mr, dict) else ""
                if path and f"已缓存 MCP 结果 {path}" not in known:
                    state.add_progress(f"已缓存 MCP 结果 {path}")
            for p in state.saved_paths:
                if p and f"已写入 {p}" not in known:
                    state.add_progress(f"已写入 {p}")
        cm.set_progress_block(state.progress_lines)
        _initial_hints: list[str] = []
        try:
            if ctx.agent.sandbox_id:
                from app.services.workplace import format_dir_listing
                listing = format_dir_listing(ctx.agent.sandbox_id, ctx.save_dir)
                if listing.strip():
                    _initial_hints.append(SystemPromptBuilder.build_workplace_listing_hint(listing))
        except Exception:
            pass
        if ctx.mcp_ids and "mcp_tool_call" not in ctx.allowed_actions:
            _initial_hints.append(
                "【配置警告】Agent 已绑定 MCP 数据源，但未开启 mcp_tool_call 权限。"
                "如需使用 MCP 工具，请在 Agent 设置中将 mcp_tool_call 添加到允许的操作列表中。"
            )
        if ctx.skill_ids and not any(
            a in ctx.allowed_actions for a in ("skill_read_md", "skill_run_script")
        ):
            _initial_hints.append(
                "【配置警告】Agent 已绑定 Skill，但未开启 skill_read_md/skill_run_script 权限。"
                "如需使用 Skill，请在 Agent 设置中添加相应权限。"
            )
        # Always establish the coach_hint layer at the front (even if empty), so
        # later in-loop hints replace it in place instead of landing mid-conversation.
        cm.push_coach_hint("\n\n".join(_initial_hints))

        # ---- Cross-turn history (history_length rounds) ----
        try:
            for role, content in _load_recent_history(ctx):
                cm.push_history_message(role, content)
        except Exception:
            logger.warning("failed to preload history", exc_info=True)

        cm.push_user_message(ctx.user_message)

        # ---- Resolve sandbox ----
        sandbox = ctx.sandbox
        if not sandbox and getattr(ctx.agent, 'sandbox_id', None):
            try:
                from app.services import docker_service
                sandbox = docker_service.get_sandbox(ctx.db, ctx.agent.sandbox_id)
            except Exception:
                pass

        # ---- Snapshot prior-run root deliverables so this run doesn't re-claim them ----
        try:
            from app.services.workplace import list_deliverable_files
            pre_existing_deliverables = set(list_deliverable_files(
                (getattr(ctx.agent, "sandbox_id", None) or "").strip() or "default"
            ))
        except Exception:
            logger.warning("failed to snapshot prior deliverables", exc_info=True)

        # ---- Visible load steps ----
        skill_names = list(ctx.skill_names or [])
        if not skill_names and ctx.skill_mds:
            skill_names = [n for n, _md in ctx.skill_mds if n]
        mcp_names = list(ctx.mcp_names or [])
        if skill_names:
            await self._append_step(ctx, state, {
                "type": "info",
                "action": "skill_loaded",
                "title": f"已加载 Skills: {', '.join(skill_names)}",
                "status": "done",
            })
        elif ctx.skill_ids:
            await self._append_step(ctx, state, {
                "type": "info",
                "action": "skill_loaded",
                "title": "Skill 绑定异常：未能读取已配置 Skill（检查 Skill 是否仍存在或文件是否可读）",
                "status": "error",
                "content": "skill_ids=" + ",".join(str(s) for s in ctx.skill_ids[:8]),
            })
        if mcp_names:
            await self._append_step(ctx, state, {
                "type": "info",
                "action": "mcp_loaded",
                "title": f"已加载 MCPs: {', '.join(mcp_names)}",
                "status": "done",
            })
        initial_model_route_event = _model_route_event(
            ctx, state, duration_ms=getattr(ctx, "model_route_duration_ms", 0),
        )
        if initial_model_route_event:
            state.model_route_events.append(initial_model_route_event)
            await self._append_step(ctx, state, {
                "type": "model_route",
                "action": "model_route",
                "title": "模型路由",
                "status": "done",
                "detail": initial_model_route_event,
                "collapsed": True,
            })
        if getattr(state, "resumed", False) and state.subtasks:
            done_count = sum(1 for s in state.subtasks if s.get("status") == "done")
            await self._append_step(ctx, state, {
                "type": "info",
                "action": "resumed",
                "title": f"检测到未完成运行，从断点续跑（已完成 {done_count}/{len(state.subtasks)} 子任务）",
                "status": "done",
            })

        # ---- Main loop ----
        logger.info(
            "modular_loop start agent=%s session=%s max_iters=%d",
            ctx.agent.id, ctx.session_id, max_iters,
        )
        tool_call_count = 0
        text_only_streak = 0
        _last_text_only_reply = ""
        llm_failures = 0
        transport_failures = 0
        reflect_fail_count = 0
        tool_fail_streak: dict[str, int] = {}
        last_tool_sig: str | None = None
        same_sig_run = 0
        cached_reference_streak = 0
        tool_call_tally: dict[str, int] = {}  # explore-loop tally keyed on mcp:<tool>
        tool_materialize_seq = 0  # READ/SHELL oversized-result dump sequence (R2)
        route_fallbacks = list(getattr(ctx, "model_route_fallbacks", []) or [])
        route_fallback_used = False

        async with McpSessionManager(
            query_cache=state.query_cache,
            mcp_results=state.mcp_results,
            run_ts=run_ts,
            sandbox=sandbox,
            large_result_chars=tool_result_clip,
        ) as mcp_sessions:
            for iteration in range(max_iters):
                if not _running.get(ctx.chat_key, False):
                    logger.info("modular_loop cancelled agent=%s iter=%d", ctx.agent.id, iteration)
                    _clear_run_state(ctx)
                    return _ret(state.final or "任务已取消")

                round_no = iteration + 1
                tool_executor = getattr(ctx, "tool_executor", None)
                if tool_executor is not None and hasattr(tool_executor, "record_iteration"):
                    tool_executor.record_iteration(round_no)
                remaining = max_iters - iteration - 1
                made_progress = False  # react-engine-v14 R1: reset per round
                hint = _budget_near_hint(remaining)
                if hint:
                    cm.add_coach_hint(hint)
                cm.flush_coach_hints()
                await self._append_step(ctx, state, {
                    "type": "llm",
                    "iteration": round_no,
                    "title": f"LLM 推理 (第 {round_no} 轮)",
                    "status": "running",
                })

                # 1. Call LLM
                llm_call_started = time.monotonic()
                try:
                    result = await chat_completion(
                        ctx.llm,
                        list(cm.messages),
                        max_tokens=8192,
                        db=ctx.db,
                        timeout=llm_timeout,
                        cancel_check=lambda: not _running.get(ctx.chat_key, False),
                        tools=tool_schemas,
                        collect_native=True,
                    )
                except Exception as exc:
                    if isinstance(exc, ChatStopped) or not _running.get(ctx.chat_key, False):
                        await self._patch_last_step(ctx, state, status="error", content="已停止")
                        _clear_run_state(ctx)
                        return _ret(state.final or "[已停止]")
                    fallback_eligible = (
                        not route_fallback_used
                        and iteration == 0
                        and not state.resumed
                        and not state.last_reply
                        and tool_call_count == 0
                        and bool(getattr(ctx, "model_route_decision_id", ""))
                        and isinstance(exc, (asyncio.TimeoutError, LLMTransportError, LLMProviderThrottled))
                        and bool(route_fallbacks)
                    )
                    if fallback_eligible:
                        fallback = route_fallbacks.pop(0)
                        from app.models import LLMResource

                        fallback_llm = ctx.db.get(LLMResource, fallback.llm_id)
                        if fallback_llm is not None:
                            from dataclasses import replace
                            from app.services.model_router import persist_fallback_route_decision

                            decision = persist_fallback_route_decision(
                                ctx.db,
                                agent_id=ctx.agent.id,
                                session_id=ctx.session_id,
                                policy_id=ctx.model_route_policy_id,
                                policy_version=ctx.model_route_policy_version,
                                parent_decision_id=ctx.model_route_decision_id,
                                candidate=fallback,
                                failure_class=type(exc).__name__,
                            )
                            ctx = replace(
                                ctx,
                                llm=fallback_llm,
                                model_route_decision_id=decision.id,
                                model_route_fallbacks=[],
                            )
                            state.model_route_decision_id = decision.id
                            state.frozen_llm_id = fallback_llm.id
                            route_fallback_used = True
                            fallback_event = _model_route_event(
                                ctx,
                                state,
                                duration_ms=int((time.monotonic() - llm_call_started) * 1000),
                            )
                            if fallback_event:
                                state.model_route_events.append(fallback_event)
                            logger.warning(
                                "model route pre-output fallback agent=%s session=%s failure=%s",
                                ctx.agent.id, ctx.session_id, type(exc).__name__,
                            )
                            await self._patch_last_step(
                                ctx, state, status="error", content=(
                                    f"{type(exc).__name__}: 首次模型请求失败，已切换到配置的回退模型。"
                                ),
                            )
                            if fallback_event:
                                await self._append_step(ctx, state, {
                                    "type": "model_route",
                                    "action": "model_route_fallback",
                                    "title": "模型路由回退",
                                    "status": "done",
                                    "detail": fallback_event,
                                    "collapsed": True,
                                })
                            continue
                    # R3: transport errors (self-healing) and HTTP/other errors use
                    # separate consecutive-failure thresholds — 3 vs 2 — so a transient
                    # network blip doesn't abort as fast as a parameter/request error.
                    is_throttled = isinstance(exc, LLMProviderThrottled)
                    is_transport = isinstance(exc, LLMTransportError)
                    if is_throttled:
                        llm_failures += 1
                        failures = llm_failures
                        threshold = 1
                    elif is_transport:
                        transport_failures += 1
                        failures = transport_failures
                        threshold = 3
                    else:
                        llm_failures += 1
                        failures = llm_failures
                        threshold = 2
                    error_detail = f"{type(exc).__name__}: {exc!r}"
                    logger.error(
                        "LLM call failed iter=%s type=%s error=%r",
                        iteration, type(exc).__name__, exc, exc_info=True,
                    )
                    await self._patch_last_step(
                        ctx, state, status="error", content=error_detail[:400],
                    )
                    if failures >= threshold:
                        persisted = _has_resumable_state(state)
                        if persisted:
                            _save_run_state(ctx, state)
                        return _ret(
                            state.final
                            or (
                                "LLM 服务连续调用失败，任务已暂停；本轮执行记录已保留，请稍后继续。"
                                if persisted
                                else "LLM 服务连续调用失败，任务已暂停，请稍后继续。"
                            )
                        )
                    continue

                llm_failures = 0
                transport_failures = 0
                native = result.has_tool_calls
                output_truncated = bool(getattr(result, "output_truncated", False))
                if native:
                    reply = (result.content or "").strip()
                    tool_steps = tool_steps_from_tool_calls(result.tool_calls)
                else:
                    reply = result.text or ""
                    tool_steps = extract_tool_steps(reply)

                state.last_reply = reply
                preview = (
                    self._native_tool_step_preview(tool_steps)
                    if native else self._llm_step_preview(reply)
                )
                await self._patch_last_step(
                    ctx, state, status="done", preview=preview,
                )
                if native:
                    cm.push_assistant_native(result.content or "", result.tool_calls)
                elif reply.strip():
                    cm.push_assistant_reply(_history_reply(reply))

                # v17 R3: length truncation after bounded continuations — never accept
                # FINAL / completion-signal from a mid-sentence reply.
                if output_truncated:
                    cm.add_coach_hint(
                        "【输出截断】上一轮模型输出因长度限制被截断，不能作为最终答案。"
                        "请从断点继续输出完整内容，或调用工具补全后再输出一行 "
                        "`FINAL: <完整总结>`。"
                    )
                    _apply_no_progress_hint(made_progress, round_no)
                    state.completion_signal_streak = 0
                    continue

                # 2a. PLAN → parse subtasks, update task_context, checkpoint (soft, LLM-owned)
                plan_steps = [s for s in tool_steps if s.action == "plan"]
                if plan_steps:
                    if getattr(state, "fix_only_until_final", False):
                        # Effective Verifier FAIL: execute the fix list; do not replan.
                        await self._append_step(ctx, state, {
                            "type": "info",
                            "action": "fix_only_dropped_plan",
                            "iteration": round_no,
                            "title": "修复期忽略重新规划",
                            "status": "done",
                            "content": "完成度复核未通过后请按修复清单执行，本轮 PLAN 未应用。",
                        })
                        cm.add_coach_hint(
                            "【修复执行】完成度复核未通过后不要重新规划，"
                            "请按修复清单调用工具执行未完成项。"
                        )
                        tool_steps = [s for s in tool_steps if s.action != "plan"]
                        plan_steps = []
                    else:
                        plan_text = plan_steps[-1].reply[len("PLAN:"):].strip()
                        prev_done = sum(1 for s in state.subtasks if s.get("status") == "done")
                        _apply_plan(ctx, state, cm, plan_text)
                        new_done = sum(1 for s in state.subtasks if s.get("status") == "done")
                        if new_done > prev_done:
                            made_progress = True  # react-engine-v14 R1: 子任务推进
                        tool_call_tally.clear()  # new PLAN distilled → restart explore tally
                        tool_steps = [s for s in tool_steps if s.action != "plan"]

                # 2a′. Completion-signal soft-conversion (react-engine-v16 R1): a
                # tool-free reply that reads as a positive completion declaration is
                # a *soft* signal — it needs N consecutive rounds + an LLM confirm
                # before it is escalated to a FINAL candidate. Never a hard gate.
                completion_candidate: str | None = None
                if not tool_steps and not plan_steps:
                    cur = _strip_reasoning_blocks(reply).strip()
                    if cur and _looks_like_completion_declaration(cur):
                        state.completion_signal_streak += 1
                        if (
                            (state.saved_paths or state.files_written > 0)
                            and _is_duplicate_reply(_last_text_only_reply, cur)
                            and state.completion_signal_streak >= _COMPLETION_SIGNAL_DUPLICATE_FINAL_AT
                        ):
                            completion_candidate = cur
                        elif state.completion_signal_streak >= _COMPLETION_SIGNAL_CONFIRM_AT:
                            if await self._confirm_completion_signal(ctx, cur):
                                completion_candidate = cur
                            else:
                                state.completion_signal_streak = 0
                    else:
                        state.completion_signal_streak = 0

                # 2b. FINAL → Verifier. PLAN is LLM-owned progress context, not a
                # delivery gate; only validated verifier evidence gaps can continue it.
                final_step = next((s for s in tool_steps if getattr(s, "is_final", False)), None)

                if final_step is not None or completion_candidate is not None:
                    # FINAL wins over any same-round tool steps (silent drop). Surface
                    # the dropped tools so the model/user see why they didn't run.
                    skipped = [s for s in tool_steps if not getattr(s, "is_final", False)]
                    if skipped:
                        names = ", ".join(f"[{s.action}]" for s in skipped)
                        await self._append_step(ctx, state, {
                            "type": "info",
                            "action": "final_skipped_tools",
                            "iteration": round_no,
                            "title": f"FINAL 与工具同轮，已忽略: {names}",
                            "status": "done",
                            "content": f"FINAL 优先于同轮工具，以下未执行: {names}",
                        })
                        cm.add_coach_hint(
                            f"【FINAL 同轮工具】本轮同时输出了 FINAL 与工具 {names}，"
                            "这些工具未执行。若确需其结果，请在 FINAL 前单独一轮输出。"
                        )
                        # Native pairing: backfill synthetic role:tool results for the
                        # skipped native calls so no assistant tool_calls is orphaned.
                        for s in skipped:
                            if s.tool_call_id:
                                cm.push_native_tool_result(
                                    s.tool_call_id,
                                    "FINAL 优先，未执行",
                                    clip=tool_result_clip,
                                )
                    candidate = (
                        _final_text(final_step.reply or reply)
                        if final_step is not None
                        else completion_candidate
                    )
                    report = await self._reflect_final(ctx, state, candidate)
                    if report is None:
                        state.fix_only_until_final = False
                        state.final = candidate
                        _clear_run_state(ctx)
                        logger.info(
                            "modular_loop finish agent=%s iter=%d/%d tools=%d",
                            ctx.agent.id, iteration, max_iters, tool_call_count,
                        )
                        return _ret(state.final)
                    missing = (report.get("missing") or "任务尚未完成").strip()
                    cards = report.get("gaps") or []
                    gaps = _validated_gap_cards(state, cards)
                    if not gaps:
                        # Prose/legacy FAILs and already-covered gaps are non-blocking.
                        state.fix_only_until_final = False
                        exhausted = _exhausted_gap_cards(state, cards)
                        if exhausted:
                            high_risk = any(_is_high_risk_gap_action(str(gap.get("action") or "")) for gap in exhausted)
                            state.terminal_reason = "high_risk_gap_exhausted" if high_risk else "low_risk_gap_exhausted"
                            suffix = (
                                "\n\n未完成的高风险动作需要你的确认/授权后才能执行。"
                                if high_risk else
                                "\n\n以上为基于现有证据的条件性结论；仍有未验证项。"
                            )
                            state.final = candidate + suffix
                        else:
                            state.final = candidate
                        _clear_run_state(ctx)
                        logger.info(
                            "modular_loop finish agent=%s iter=%d/%d tools=%d vague_fail_as_pass=1",
                            ctx.agent.id, iteration, max_iters, tool_call_count,
                        )
                        return _ret(state.final)
                    effective = [gap["action"] for gap in gaps]
                    for gap in gaps:
                        existing = state.verifier_gaps.setdefault(gap["gap_id"], {"card": gap, "signatures": [], "evidence": []})
                        existing["card"] = gap
                    reflect_fail_count += 1
                    if final_step is not None and final_step.tool_call_id:
                        cm.push_native_tool_result(
                            final_step.tool_call_id,
                            "完成度复核未通过，FINAL 未接受；请按修复清单继续执行。",
                            clip=tool_result_clip,
                        )
                    if reflect_fail_count >= _REFLECT_FAIL_CONVERGE:
                        # Consecutive effective Verifier rejections → converge (D4).
                        state.fix_only_until_final = False
                        state.final = candidate
                        await self._append_step(ctx, state, {
                            "type": "info",
                            "action": "reflect_converged",
                            "iteration": round_no,
                            "title": "完成度复核连续拒绝，已收敛接受候选",
                            "status": "done",
                            "content": missing[:300],
                        })
                        logger.info(
                            "modular_loop reflect_converged agent=%s iter=%d/%d rejects=%d",
                            ctx.agent.id, iteration, max_iters, reflect_fail_count,
                        )
                        _clear_run_state(ctx)
                        return _ret(state.final)
                    state.fix_only_until_final = True
                    _save_run_state(ctx, state)
                    await self._append_step(ctx, state, {
                        "type": "info",
                        "action": "replan",
                        "iteration": round_no,
                        "title": "完成度复核未通过，请按修复清单执行",
                        "status": "done",
                        "content": missing[:300],
                    })
                    hint = "【完成度反思】" + missing
                    hint += "\n修复清单：\n" + "\n".join(
                        f"- {item}" for item in effective[:12]
                    )
                    hint += "\n请按修复清单调用工具执行，不要重新规划。"
                    cm.add_coach_hint(hint)
                    _apply_no_progress_hint(made_progress, round_no)
                    state.completion_signal_streak = 0
                    continue

                # 2b. Text/plan reply with no tool — auto-rescue bare code, else nudge,
                # then hard-stop if it keeps happening.
                if not tool_steps:
                    cur = _strip_reasoning_blocks(reply).strip()
                    is_dup = bool(_last_text_only_reply) and _is_duplicate_reply(_last_text_only_reply, cur)

                    # Auto-rescue: the model emitted bare shell/Python but forgot the
                    # protocol prefix. Execute it faithfully (still gated by allowed_actions
                    # below) instead of letting it accumulate as inert text and hit the stop.
                    rescued_cmd = (
                        _rescue_leaked_code(cur) if "shell" in ctx.allowed_actions else None
                    )
                    if rescued_cmd:
                        from app.services.tool_parser import ToolStep

                        await self._append_tool_step(
                            ctx, state,
                            iteration=round_no,
                            action="shell",
                            title="[shell] 自动救援（模型漏写协议前缀，已转 SHELL 执行）",
                            status="done",
                        )
                        tool_steps = [ToolStep("shell", f"SHELL: {rescued_cmd}")]
                    else:
                        if plan_steps:
                            cm.add_coach_hint(
                                "【规划待执行】你刚输出了 PLAN 但本轮没有调用任何工具。"
                                "PLAN 只记录计划，工具要靠单独一行的协议调用才会真正执行："
                                "现在对第一个 `[ ]` 子任务输出实际的工具调用行（格式见上方工具目录），"
                                "执行后再回来把该子任务标成 `[x]`。不要重复输出 PLAN。"
                            )
                        text_only_streak += 1
                        if _looks_like_leaked_tool_call(cur) or _looks_like_raw_code(cur):
                            cm.add_coach_hint(
                                _build_format_hint(cur, bare_python=_looks_like_bare_python(cur))
                            )
                        elif is_dup:
                            cm.add_coach_hint(_build_stuck_hint(cur, state))
                        elif not cur:
                            cm.add_coach_hint(
                                "【提示】本轮模型输出为空，任务未推进。请调用工具（无依赖可同轮多个）继续，或输出一行 `FINAL: <总结>` 结束本轮。"
                            )
                        elif text_only_streak >= 2:
                            progress = [ln for ln in state.progress_lines if ln.strip()][-8:]
                            prog = "\n".join(f"- {ln}" for ln in progress) if progress else "（暂无）"
                            cm.add_coach_hint(
                                f"【提示】你已连续 {text_only_streak} 轮只输出文字，没有调用工具也没有输出 FINAL。"
                                "请立即二选一：调用工具（无依赖可同轮多个）推进任务，或输出一行 `FINAL: <当前进度/结果>` 结束本轮。\n"
                                "本轮已完成：\n" + prog
                            )
                        _last_text_only_reply = cur
                        _apply_no_progress_hint(made_progress, round_no)
                        continue

                text_only_streak = 0
                _last_text_only_reply = ""
                state.completion_signal_streak = 0

                # 3. Execute each parsed tool step (security-gated)
                for step in tool_steps:
                    if not _running.get(ctx.chat_key, False):
                        logger.info("tool execution cancelled agent=%s iter=%d", ctx.agent.id, iteration)
                        _clear_run_state(ctx)
                        return _ret(state.final or "[已停止]")
                    action = step.action
                    normalized = step.reply
                    if not action or not normalized:
                        continue

                    if action == "mcp_route_request":
                        if state.mcp_route_attempts >= 2:
                            result_text = "MCP 补选已达本次运行上限（2 次），请基于现有证据继续或向用户澄清。"
                        else:
                            state.mcp_route_attempts += 1
                            decision = await route_mcp_candidates(
                                llm=ctx.llm, db=ctx.db,
                                user_message=state.goal or ctx.user_message,
                                candidates=candidates,
                                selected_mcp_ids=state.selected_mcp_ids,
                                trigger=normalized[:500], timeout=llm_timeout,
                            )
                            newly_selected = [
                                mid for mid in decision.selected_mcp_ids
                                if mid not in state.selected_mcp_ids
                            ]
                            mcp_load_results = []
                            if newly_selected:
                                state.selected_mcp_ids.extend(newly_selected)
                                tools_block = await SystemPromptBuilder.build_tools_desc(
                                    db=ctx.db, agent=ctx.agent, allowed=ctx.allowed_actions,
                                    skill_ids=ctx.skill_ids, mcp_ids=state.selected_mcp_ids,
                                    rag_ids=ctx.rag_ids, httpmcp_ids=ctx.httpmcp_ids,
                                    save_dir=ctx.save_dir, im_source=ctx.im_source,
                                )
                                cm.set_tools_catalog(tools_block)
                                mcp_load_results = getattr(tools_block, "mcp_load_results", [])
                                result_text = f"已补选并加载 MCP: {', '.join(newly_selected)}"
                            else:
                                result_text = "未找到可新增的 MCP 能力；请基于现有工具继续或向用户澄清。"
                            _record_mcp_route_event(
                                state, user_message=state.goal or ctx.user_message,
                                candidates=candidates, decision=decision,
                                trigger="agent_capability_request",
                                supplement_index=state.mcp_route_attempts,
                                mcp_load_results=mcp_load_results,
                            )
                        await self._append_tool_step(
                            ctx, state, iteration=round_no, action=action,
                            title="请求补选 MCP 能力", status="done", content=result_text,
                        )
                        cm.add_coach_hint(f"【MCP 补选】{result_text}")
                        continue

                    if action not in ctx.allowed_actions:
                        cm.add_coach_hint(
                            f"工具 `{action}` 未启用，已阻止执行。请改用当前工具目录中的能力。"
                        )
                        await self._append_tool_step(
                            ctx, state,
                            iteration=round_no,
                            action="permission_denied",
                            title=f"已阻止未启用工具: {action}",
                            status="error",
                            content="Agent 权限配置未启用该工具，未执行。",
                        )
                        # Native pairing: still backfill a synthetic role:tool result so
                        # the next round's assistant tool_calls has a paired tool message.
                        if step.tool_call_id:
                            cm.push_native_tool_result(
                                step.tool_call_id,
                                f"已阻止未启用工具: {action}（未执行）",
                                clip=tool_result_clip,
                            )
                        continue

                    if action == "recall":
                        query = normalized[len("RECALL:"):].strip() if normalized.startswith("RECALL:") else normalized
                        result_text = cm.recall(query)
                        err = ""
                        cache_hit = False
                    else:
                        err = ""
                        desc = dedup_descriptor(action, normalized, sandbox)
                        cached = None
                        if desc is not None:
                            entry = state.query_cache.get(desc["key"])
                            if entry is not None and _dedup_entry_valid(desc, entry):
                                cached = entry
                        cache_hit = cached is not None
                        if cached is not None:
                            # READ/SHELL/SEARCH dedup hit: reference the earlier result
                            # instead of re-reading / re-running (R2).
                            result_text = _dedup_hit_pointer(action, cached)
                        else:
                            try:
                                tool_executor = getattr(ctx, "tool_executor", None)
                                if tool_executor is not None:
                                    tool_result = await tool_executor(action, normalized)
                                else:
                                    tool_result = await execute_action(
                                        action, normalized,
                                        ctx.db, ctx.agent, sandbox,
                                        ctx.skill_ids, state.selected_mcp_ids,
                                        ctx.rag_ids,
                                        httpmcp_ids=ctx.httpmcp_ids,
                                        mcp_sessions=mcp_sessions,
                                    )
                            except Exception as exc:
                                if tool_executor is not None:
                                    from app.services.code_agent.runner import (
                                        RunnerCommandTimeout,
                                        RunnerUnavailableError,
                                    )
                                    if isinstance(exc, (RunnerCommandTimeout, RunnerUnavailableError)):
                                        raise
                                tool_result = f"工具执行异常: {exc}"
                                err = f"{type(exc).__name__}: {exc}"[:400]
                            result_text = tool_result or ""
                            if desc is not None:
                                state.query_cache[desc["key"]] = _dedup_entry(desc, action, result_text)
                    if (
                        action == "mcp_tool_call"
                        and state.mcp_route_attempts < 2
                        and (
                            "未在绑定 MCP 中找到工具" in result_text
                            or _is_mcp_connect_failure(result_text)
                        )
                    ):
                        state.mcp_route_attempts += 1
                        decision = await route_mcp_candidates(
                            llm=ctx.llm, db=ctx.db,
                            user_message=state.goal or ctx.user_message,
                            candidates=candidates,
                            selected_mcp_ids=state.selected_mcp_ids,
                            trigger=f"mcp_failure:{normalized}"[:500], timeout=llm_timeout,
                        )
                        newly_selected = [mid for mid in decision.selected_mcp_ids if mid not in state.selected_mcp_ids]
                        if newly_selected:
                            state.selected_mcp_ids.extend(newly_selected)
                            tools_block = await SystemPromptBuilder.build_tools_desc(
                                db=ctx.db, agent=ctx.agent, allowed=ctx.allowed_actions,
                                skill_ids=ctx.skill_ids, mcp_ids=state.selected_mcp_ids,
                                rag_ids=ctx.rag_ids, httpmcp_ids=ctx.httpmcp_ids,
                                save_dir=ctx.save_dir, im_source=ctx.im_source,
                            )
                            cm.set_tools_catalog(tools_block)
                            cm.add_coach_hint(
                                f"【MCP 自动补选】已因工具不可用补充加载: {', '.join(newly_selected)}。"
                            )
                        _record_mcp_route_event(
                            state, user_message=state.goal or ctx.user_message,
                            candidates=candidates, decision=decision,
                            trigger=(
                                "selected_mcp_tool_missing"
                                if "未在绑定 MCP 中找到工具" in result_text
                                else "selected_mcp_unreachable"
                            ),
                            supplement_index=state.mcp_route_attempts,
                            mcp_load_results=getattr(tools_block, "mcp_load_results", []) if newly_selected else [],
                        )
                    cached_mcp_reference = action == "mcp_tool_call" and _is_cached_reference(result_text)
                    if cached_mcp_reference:
                        cache_hit = True
                    action_signature = _gap_action_signature(normalized)
                    for gap in (state.verifier_gaps or {}).values():
                        card = gap.get("card") if isinstance(gap, dict) else None
                        if isinstance(card, dict) and card.get("signature") == action_signature and not cache_hit:
                            signatures = gap.setdefault("signatures", [])
                            if action_signature not in signatures:
                                signatures.append(action_signature)
                            gap.setdefault("evidence", []).append(result_text[:500])
                    cm.push_archive(_archive_type_for(action), result_text)

                    tool_call_count += 1

                    sig = f"{action}:{normalized.strip()}"
                    if sig == last_tool_sig:
                        same_sig_run += 1
                    else:
                        last_tool_sig = sig
                        same_sig_run = 1

                    # MCP 连接级失败（端点不可达/超时）按「MCP 连接」键控，跨工具名累加：
                    # 端点一旦 down，换任何工具名都会失败，按精确 tool+args 键控会被打散、永不触发。
                    fail_key = (
                        "mcp_connect"
                        if action == "mcp_tool_call" and _is_mcp_connect_failure(result_text)
                        else sig
                    )

                    if bool(err) or _tool_result_failed(result_text):
                        cached_reference_streak = 0
                        streak = tool_fail_streak.get(fail_key, 0) + 1
                        tool_fail_streak[fail_key] = streak
                        hint = _missing_dep_hint(action, result_text) or _tool_fail_hint(
                            action, normalized.strip(), streak, soft_circuit
                        )
                        if hint:
                            cm.add_coach_hint(hint)
                    else:
                        tool_fail_streak[fail_key] = 0
                        if cached_mcp_reference:
                            cached_reference_streak += 1
                            hint = _cached_reference_recovery_hint(result_text, ctx.allowed_actions)
                            if hint:
                                cm.add_coach_hint(hint)
                            if cached_reference_streak >= _CACHED_REFERENCE_HARD_STOP_AT:
                                reason = (
                                    f"连续 {cached_reference_streak} 次重复请求已缓存的 MCP 结果，"
                                    "自动停止避免资源浪费"
                                )
                                state.final = _forced_stop_reply(state, reason)
                                _clear_run_state(ctx)
                                logger.info(
                                    "modular_loop cached_reference_converged agent=%s iter=%d/%d streak=%d",
                                    ctx.agent.id, iteration, max_iters, cached_reference_streak,
                                )
                                return _ret(state.final)
                        elif not cache_hit:
                            cached_reference_streak = 0
                        if not cache_hit:
                            made_progress = True  # react-engine-v14 R1: 工具执行成功
                            reflect_fail_count = 0  # real exec resets consecutive effective FAIL
                        if same_sig_run >= 3 and (same_sig_run - 3) % 2 == 0:
                            cm.add_coach_hint(
                                _tool_repeat_hint(action, normalized.strip(), same_sig_run)
                            )
                        if action == "mcp_tool_call":
                            m = re.match(r"MCP:\s*(\S+)", normalized)
                            tool_name = (m.group(1) if m else "mcp").strip().strip("`'\"")
                            dkey = f"mcp:{tool_name}"
                            n = tool_call_tally.get(dkey, 0) + 1
                            tool_call_tally[dkey] = n
                            if n >= 4 and (n - 4) % 2 == 0:
                                cm.add_coach_hint(_distill_hint(tool_name, n))
                            if _is_cached_reference(result_text):
                                _echo_materialized_manifest(state, cm, reason="查询命中缓存")

                    if action == "file_write" and result_text:
                        m = re.search(r"已写入\s+([^\n]+)", result_text)
                        if m:
                            path = m.group(1)
                            if path not in state.saved_paths:
                                state.saved_paths.append(path)
                            state.files_written += 1
                            state.add_progress(f"已写入 {path}")
                            # R3: a WRITE overwrites the path — drop any cached READ
                            # so the next READ re-reads instead of serving stale content.
                            state.query_cache.pop(read_cache_key(path), None)

                    # MCP oversized-result materialization + query dedup live in
                    # McpSessionManager (task 4.5); R2 dumps oversized READ/SHELL
                    # results here instead — full text to task/<ts>/, context gets
                    # a short path+preview+length echo (mirrors mcp_result_*.json).
                    ctx_result, dumped_path = _materialize_tool_result(
                        action, result_text,
                        run_ts=run_ts, sandbox=sandbox, seq=tool_materialize_seq,
                    )
                    if dumped_path:
                        tool_materialize_seq += 1
                    if native and step.tool_call_id:
                        cm.push_native_tool_result(step.tool_call_id, ctx_result, clip=tool_result_clip)
                    else:
                        cm.push_tool_result(ctx_result, action=action, clip=tool_result_clip)
                    await self._append_tool_step(
                        ctx, state,
                        iteration=round_no,
                        action=action,
                        title=_tool_step_title(action, normalized),
                        status="error" if err else "done",
                        content=err or result_text[:_EXECUTION_DETAIL_LIMIT],
                    )

                if state.progress_lines:
                    cm.set_progress_block(state.progress_lines)

                # Completed-work checklist (react-engine-v16 R3): rebuild each round
                # so the stable task_context layer always echoes what's done.
                cm.set_completed_checklist(_build_completed_checklist(state, tool_call_tally))

                # Trim context periodically
                if iteration > 0 and iteration % 8 == 0:
                    cm.trim_tool_results()

                # react-engine-v15 R1′: end-of-round no-progress breakthrough hint.
                _apply_no_progress_hint(made_progress, round_no)

        # Budget exhausted
        logger.warning(
            "modular_loop budget_exhausted agent=%s iters=%d/%d tools=%d",
            ctx.agent.id, max_iters, max_iters, tool_call_count,
        )
        tool_executor = getattr(ctx, "tool_executor", None)
        if tool_executor is not None and hasattr(tool_executor, "mark_iteration_budget_exhausted"):
            tool_executor.mark_iteration_budget_exhausted()
        reason = f"达到 {max_iters} 轮上限且未收到 FINAL 结束信号"
        final = await self._distill_final(ctx, state, reason)
        pending = [s for s in state.subtasks if (s.get("status") or "pending") != "done"]
        if not pending:
            # Short task (no resumable subtasks) → the distilled summary closes the run.
            _clear_run_state(ctx)
        else:
            # Persist the latest state (not just the last PLAN snapshot) so the next
            # message resumes with all materialized results / dedup / progress intact.
            _save_run_state(ctx, state)
        return _ret(final)


def _capture_deliverable_paths(ctx, state, pre_existing: set[str] | None = None) -> None:
    """Observational: record workplace deliverable files for exec-summary download buttons."""
    sid = (getattr(ctx.agent, "sandbox_id", None) or "").strip() or "default"
    try:
        from app.services.workplace import list_deliverable_files
        for rel in list_deliverable_files(sid):
            if rel in (pre_existing or set()):
                continue
            if rel not in state.saved_paths:
                state.saved_paths.append(rel)
    except Exception:
        pass


def _context_available_percent(ctx, cm) -> int:
    """react-engine-v15 R4: real context-availability % (pre-trim, shared口径).

    Sums ``estimate_messages_tokens`` over the current (untrimmed) context layers
    and divides by ``llm.max_context_tokens``. Display-only — never feeds into
    trimming decisions (fit_messages_to_context keeps its own budget). Clamped
    0–100; falls back to 100 on any error so a failed estimate never misleads.
    """
    try:
        from app.services.llm_client import estimate_messages_tokens

        used = estimate_messages_tokens(cm.messages)
        max_tokens = int(getattr(ctx.llm, "max_context_tokens", None) or 128000)
        if max_tokens <= 0:
            return 100
        pct = int(round((1.0 - used / max_tokens) * 100.0))
        return max(0, min(100, pct))
    except Exception:
        logger.warning("context_available_percent failed", exc_info=True)
        return 100


# ---- Long-task: subtask tracking + cross-restart resume ----

def _render_subtask_list(subtasks: list[dict]) -> str:
    """Render an ordered subtask list with checkbox status, one per line."""
    lines = []
    for s in subtasks or []:
        if not isinstance(s, dict):
            continue
        mark = "x" if s.get("status") == "done" else " "
        text = (s.get("text") or "").strip()
        if text:
            lines.append(f"- [{mark}] {text}")
    return "\n".join(lines)


def _build_completed_checklist(state, tool_call_tally: dict | None = None) -> str:
    """Zero-LLM completed-work checklist (react-engine-v16 R3).

    Rebuilt each round from the five persisted/tracked sources so the model sees
    an up-to-date picture of what's already done and never redoes it: subtasks /
    saved_paths / progress_lines / tool_call_tally / query_cache. Lives in the
    task_context layer, so it is echoed every round and never trimmed.
    """
    tally = tool_call_tally or {}
    blocks: list[str] = []
    subtasks_txt = _render_subtask_list(state.subtasks)
    if subtasks_txt:
        blocks.append(f"【已完成子任务】\n{subtasks_txt}")
    saved = [p for p in (state.saved_paths or []) if p]
    if saved:
        blocks.append("【已写文件】\n" + "\n".join(f"- {p}" for p in saved[-20:]))
    tools: dict[str, int] = {}
    for k, n in tally.items():
        name = str(k).replace("mcp:", "", 1)
        tools[name] = tools.get(name, 0) + int(n)
    for v in (getattr(state, "query_cache", {}) or {}).values():
        if isinstance(v, dict) and v.get("tool"):
            tools.setdefault(v["tool"], 0)
    if tools:
        blocks.append(
            "【已尝试工具】"
            + "、".join(f"{name} ×{n}" if n else name for name, n in list(tools.items())[:20])
        )
    recent = (state.progress_lines or [])[-8:]
    if recent:
        blocks.append("【进度】\n" + "\n".join(f"- {ln}" for ln in recent))

    header = "【已完成清单】"
    if getattr(state, "resumed", False) and state.subtasks:
        done = sum(1 for s in state.subtasks if s.get("status") == "done")
        header += f"（上次执行到此：已完成 {done}/{len(state.subtasks)} 子任务，还差 {len(state.subtasks) - done} 项）"
    if not blocks:
        return f"{header}\n（暂无）"
    return f"{header}\n" + "\n\n".join(blocks)


def _merge_subtasks(prev: list[dict], new: list[dict]) -> list[dict]:
    """Merge a newly-parsed subtask list over the previous one.

    Preserves a previously ``done`` status by text match, so a re-emitted PLAN
    that forgot the checkbox doesn't regress a completed subtask back to pending.
    """
    done_texts = {s.get("text", "").strip() for s in (prev or []) if s.get("status") == "done"}
    out: list[dict] = []
    for s in new or []:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        status = s.get("status") or "pending"
        if status != "done" and text in done_texts:
            status = "done"
        out.append({"text": text, "status": status})
    return out


def _has_resumable_state(state) -> bool:
    """True when the run holds state worth persisting for a later resume.

    Mirrors ``_load_run_state``'s acceptance: any recoverable field being non-empty
    means a checkpoint is worth writing (D1). Used by the non-FINAL exit paths so a
    save only happens when it will actually make the next resume recoverable.
    """
    return bool(
        state.subtasks
        or getattr(state, "mcp_results", None)
        or getattr(state, "query_cache", None)
        or state.progress_lines
        or state.saved_paths
        or getattr(state, "verifier_gaps", None)
        or getattr(state, "terminal_reason", "")
    )


_ROUTE_SECRET_RE = re.compile(
    r"(?i)\b(authorization|proxy-authorization|api[_-]?key|token|password|secret)\b"
    r"\s*[\"']?\s*[:=]\s*[\"']?\s*(?:bearer\s+)?[^\s,;}&\"']+"
)
_ROUTE_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+\-/=]+")


def _redact_route_text(value: str, limit: int | None = 240) -> str:
    """Produce a bounded route-audit summary without credential values."""
    compact = " ".join(str(value or "").split())
    compact = _ROUTE_SECRET_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", compact)
    compact = _ROUTE_BEARER_RE.sub("Bearer [REDACTED]", compact)
    return compact[:limit] if limit is not None else compact


def _route_request_summary(value: str) -> str:
    """Audit request shape without retaining any user-provided text."""
    raw = str(value or "")
    credential_markers = len(_ROUTE_SECRET_RE.findall(raw)) + len(_ROUTE_BEARER_RE.findall(raw))
    nonempty_lines = sum(1 for line in raw.splitlines() if line.strip())
    return (
        f"user_request(chars={len(raw)}, nonempty_lines={nonempty_lines}, "
        f"credential_markers={credential_markers})"
    )


def _route_reason_summary(reason: str, user_message: str) -> str:
    """Redact a router reason without allowing it to echo the full request."""
    compact_reason = _redact_route_text(reason, limit=None)
    compact_request = _redact_route_text(user_message, limit=None)
    if compact_request:
        compact_reason = compact_reason.replace(compact_request, "[USER_REQUEST_REDACTED]")
    return compact_reason[:4000]


def _model_route_event(ctx, state, *, duration_ms: int = 0) -> dict | None:
    """Return a strict-whitelist model-routing audit event for the execution UI."""
    decision_id = str(getattr(state, "model_route_decision_id", "") or "").strip()
    if not decision_id:
        return None
    try:
        from app.models import ModelRouteDecision

        decision = ctx.db.get(ModelRouteDecision, decision_id)
        if decision is None:
            return None
        detail = json.loads(decision.detail or "{}")
        if not isinstance(detail, dict):
            detail = {}
    except Exception:
        return None
    exclusions = []
    for item in detail.get("exclusions") or []:
        if isinstance(item, dict):
            exclusions.append({
                "model_id": str(item.get("model_id") or "")[:64],
                "reason": str(item.get("reason") or "")[:80],
            })
    return {
        "version": 1,
        "kind": str(detail.get("kind") or "selection")[:64],
        "decision_id": decision.id,
        "policy_id": decision.policy_id,
        "policy_version": decision.policy_version,
        "role": decision.role,
        "frozen_model_id": decision.llm_id,
        "candidate_ids": [str(item)[:64] for item in (detail.get("candidate_ids") or [])[:20]],
        "exclusions": exclusions[:40],
        "failure": str(detail.get("failure") or detail.get("failure_class") or "")[:80],
        "duration_ms": max(0, int(duration_ms or 0)),
    }


def _record_mcp_route_event(
    state,
    *,
    user_message: str,
    candidates,
    decision,
    trigger: str,
    supplement_index: int,
    mcp_load_results: list[dict],
    load_failure: str = "",
) -> None:
    """Persist and log a redacted, metadata-only MCP route audit event."""
    event = {
        "request_summary": _route_request_summary(user_message),
        "candidate_mcp_ids": [str(candidate.id) for candidate in candidates],
        "selected_mcp_ids": list(decision.selected_mcp_ids),
        "reason": _route_reason_summary(decision.reason, user_message),
        "trigger": trigger,
        "supplement_index": supplement_index,
        "mcp_load_results": [
            {"mcp_id": str(row.get("mcp_id") or ""), "status": str(row.get("status") or "unknown")}
            for row in mcp_load_results if isinstance(row, dict) and row.get("mcp_id")
        ],
    }
    event["loaded_mcp_ids"] = [
        row["mcp_id"] for row in event["mcp_load_results"]
        if row["status"] == "catalog_loaded"
    ]
    event["load_result"] = (
        "catalog_loaded" if event["loaded_mcp_ids"]
        else (load_failure or (event["mcp_load_results"][0]["status"] if event["mcp_load_results"] else "no_new_selection"))
    )
    state.mcp_route_events.append(event)
    if len(state.mcp_route_events) > 20:
        del state.mcp_route_events[:-20]
    logger.info("mcp_route_event=%s", json.dumps(event, ensure_ascii=False))


def _save_run_state(ctx, state) -> None:
    """Upsert a resumable checkpoint for (agent, session).

    Persists the recoverable core — goal, subtask list (+ status), progress and
    saved files — never the full message array (history reloads from ChatMessage)
    nor the volatile tool_result layer (rebuilt on re-execution).
    """
    from app.models import AgentRunState
    from app.security import now_str

    goal = (getattr(state, "goal", "") or (ctx.user_message or "")).strip()
    payload = {
        "goal": goal,
        "subtasks": state.subtasks,
        "progress_lines": state.progress_lines,
        "saved_paths": state.saved_paths,
        "files_written": state.files_written,
        "last_reply": state.last_reply,
        "run_ts": getattr(state, "run_ts", "") or "",
        "mcp_results": (getattr(state, "mcp_results", []) or [])[-100:],
        "query_cache": getattr(state, "query_cache", {}) or {},
        "plan_text": getattr(state, "plan_text", "") or "",
        "fix_only_until_final": bool(getattr(state, "fix_only_until_final", False)),
        "verifier_gaps": getattr(state, "verifier_gaps", {}) or {},
        "terminal_reason": getattr(state, "terminal_reason", "") or "",
        "selected_mcp_ids": getattr(state, "selected_mcp_ids", []) or [],
        "mcp_route_attempts": int(getattr(state, "mcp_route_attempts", 0) or 0),
        "mcp_route_events": (getattr(state, "mcp_route_events", []) or [])[-20:],
        "model_route_events": (getattr(state, "model_route_events", []) or [])[-10:],
    }
    blob = json.dumps(payload, ensure_ascii=False)
    try:
        row = (
            ctx.db.query(AgentRunState)
            .filter(
                AgentRunState.agent_id == ctx.agent.id,
                AgentRunState.session_id == ctx.session_id,
            )
            .first()
        )
        if row:
            row.state = blob
            row.updated_at = now_str()
        else:
            ctx.db.add(AgentRunState(
                agent_id=ctx.agent.id,
                session_id=ctx.session_id,
                state=blob,
                updated_at=now_str(),
            ))
        ctx.db.commit()
    except Exception:
        logger.exception(
            "save_run_state failed agent=%s session=%s",
            getattr(ctx.agent, "id", ""), ctx.session_id,
        )


def _load_run_state(ctx):
    """Return a resumed AgentLoopState from a persisted checkpoint, or None.

    Resumable when the checkpoint holds any recoverable state (subtasks, MCP
    results, query dedup cache, progress, or saved paths).
    """
    from app.models import AgentRunState
    from app.services.agent_runtime.loop_state import AgentLoopState

    try:
        row = (
            ctx.db.query(AgentRunState)
            .filter(
                AgentRunState.agent_id == ctx.agent.id,
                AgentRunState.session_id == ctx.session_id,
            )
            .first()
        )
        if not row:
            return None
        data = json.loads(row.state or "{}")
    except Exception:
        return None
    subtasks = data.get("subtasks") or []
    mcp_results = list(data.get("mcp_results") or [])
    query_cache = dict(data.get("query_cache") or {})
    progress_lines = list(data.get("progress_lines") or [])
    saved_paths = list(data.get("saved_paths") or [])
    if not (
        subtasks or mcp_results or query_cache or progress_lines or saved_paths
        or data.get("verifier_gaps") or data.get("terminal_reason") or data.get("mcp_route_events")
    ):
        return None
    state = AgentLoopState()
    state.subtasks = subtasks
    state.progress_lines = progress_lines
    state.saved_paths = saved_paths
    state.files_written = int(data.get("files_written") or 0)
    state.last_reply = data.get("last_reply") or ""
    state.goal = (data.get("goal") or "").strip()
    state.run_ts = str(data.get("run_ts") or "")
    state.mcp_results = mcp_results
    state.query_cache = query_cache
    state.plan_text = str(data.get("plan_text") or "")
    state.fix_only_until_final = bool(data.get("fix_only_until_final"))
    state.verifier_gaps = dict(data.get("verifier_gaps") or {})
    state.terminal_reason = str(data.get("terminal_reason") or "")
    state.selected_mcp_ids = list(data.get("selected_mcp_ids") or [])
    state.mcp_route_attempts = int(data.get("mcp_route_attempts") or 0)
    state.mcp_route_events = list(data.get("mcp_route_events") or [])[-20:]
    state.model_route_events = list(data.get("model_route_events") or [])[-10:]
    state.resumed = True
    return state


def _clear_run_state(ctx) -> None:
    """Delete the checkpoint once a run ends — FINAL, user cancel, or stop.

    Cancel/stop are terminal for this (agent, session): leaving a stale
    checkpoint would auto-resume a run the user explicitly ended.
    """
    from app.models import AgentRunState

    try:
        row = (
            ctx.db.query(AgentRunState)
            .filter(
                AgentRunState.agent_id == ctx.agent.id,
                AgentRunState.session_id == ctx.session_id,
            )
            .first()
        )
        if row:
            ctx.db.delete(row)
            ctx.db.commit()
    except Exception:
        logger.exception(
            "clear_run_state failed agent=%s session=%s",
            getattr(ctx.agent, "id", ""), ctx.session_id,
        )


def _echo_materialized_manifest(state, cm, reason: str) -> None:
    """Echo the materialized MCP manifest (tool → path) into the progress block
    and a coach hint (R7). Soft only: reuses ``add_progress`` / ``add_coach_hint``,
    no counter, no threshold, no hard stop."""
    if not state.mcp_results:
        return
    known = set(state.progress_lines)
    manifest: list[str] = []
    for mr in state.mcp_results:
        if not isinstance(mr, dict):
            continue
        path = mr.get("path", "")
        tool = mr.get("tool", "")
        if not path:
            continue
        marker = f"已缓存 MCP 结果 {path}"
        if marker not in known:
            state.add_progress(marker)
            known.add(marker)
        manifest.append(f"- {tool} → {path}" if tool else f"- {path}")
    if manifest:
        cm.add_coach_hint(
            f"【已落盘清单】{reason}。以下 MCP 结果已物化落盘，可直接 READ/SHELL 复用，勿重复查询：\n"
            + "\n".join(manifest[-20:])
        )


# react-engine-v10 R1: plan preflight (local static checks, zero LLM cost).
_PLAN_ACTION_PREFIXES = {
    "SHELL": "shell",
    "READ": "file_read",
    "WRITE": "file_write",
    "SEARCH": "file_search",
    "PATCH": "file_search_replace",
    "MCP": "mcp_tool_call",
    "RAG": "rag_query",
    "RECALL": "recall",
    "RUN_SKILL": "skill_run_script",
    "SKILL_MD": "skill_read_md",
    "HTTPMCP": "httpmcp_call",
}
_PLAN_ACTION_RE = re.compile(
    r"(?<![\w])(SHELL|READ|WRITE|SEARCH|PATCH|MCP|RAG|RECALL|RUN_SKILL|SKILL_MD|HTTPMCP):"
)


def _plan_mentioned_actions(plan_text: str) -> set[str]:
    """Protocol-prefixed actions mentioned anywhere in a PLAN body (R1)."""
    mentioned: set[str] = set()
    for prefix in _PLAN_ACTION_RE.findall(plan_text or ""):
        mentioned.add(_PLAN_ACTION_PREFIXES[prefix])
    return mentioned


def _plan_preflight_hints(ctx, plan_text: str, subtasks: list[dict]) -> list[str]:
    """Local, zero-LLM static checks on a PLAN (R1). Soft hints only — never gating.

    - 越权：计划提到的动作不在 ``allowed_actions`` 内 → 提示「本 agent 无 <action> 权限」。
    - 空 / 不可解析：计划为空，或多行但未解析出子任务 → 提示用带编号 / ``[ ]`` 清单格式。
    """
    hints: list[str] = []
    allowed = set(getattr(ctx, "allowed_actions", None) or [])
    for action in sorted(_plan_mentioned_actions(plan_text) - allowed):
        hints.append(
            f"【规划前置检查】本 agent 无 `{action}` 权限，计划中提到的该动作将无法执行；"
            "请改用当前工具目录中的能力，或移除该步骤。"
        )
    stripped = (plan_text or "").strip()
    if not stripped:
        hints.append(
            "【规划前置检查】PLAN 为空，无法推进。请用带编号（1.）或 `[ ]` 清单的 PLAN 格式列出子任务。"
        )
    elif not subtasks and len([ln for ln in stripped.splitlines() if ln.strip()]) >= 2:
        hints.append(
            "【规划前置检查】计划未解析出子任务。请用带编号（1.）或 `[ ]` 清单的 PLAN 格式，每行一个子任务。"
        )
    return hints


def _apply_plan(ctx, state, cm, plan_text: str) -> None:
    """Parse a PLAN body into subtasks, update task_context, and checkpoint.

    Structured plans (≥2 subtasks) activate long-task mode and persist a
    resumable checkpoint. Free-text plans keep the short-task path unchanged.
    """
    from app.services.tool_parser import parse_subtasks

    goal = (getattr(state, "goal", "") or (ctx.user_message or "")).strip()
    # A prior plan (text or subtasks) already exists → this is a re-issued PLAN;
    # surface what's already materialized so the model doesn't redo it (R7).
    had_plan = bool((state.plan_text or "").strip()) or bool(state.subtasks)
    state.plan_text = plan_text or ""
    subtasks = parse_subtasks(plan_text or "")
    for hint in _plan_preflight_hints(ctx, plan_text, subtasks):
        cm.add_coach_hint(hint)
    if subtasks:
        state.subtasks = _merge_subtasks(state.subtasks, subtasks)
        plan_for_context = _render_subtask_list(state.subtasks)
        long_task = bool(state.subtasks)
        # Soft nudge (no counting/gating): keep the checkbox list fresh so a later
        # resume sees accurate progress.
        cm.add_coach_hint(
            "【子任务推进】完成一个子任务后，请在下一轮重新输出带 [x] 标记的完整 PLAN，让进度可追踪。"
        )
    else:
        plan_for_context = plan_text
        long_task = False
    cm.set_task_context(
        goal=goal,
        plan=plan_for_context,
        view_catalog=build_ads_view_catalog(getattr(ctx, "mcp_ids", None) or []),
    )
    if had_plan:
        _echo_materialized_manifest(state, cm, reason="重发 PLAN")
    if long_task:
        _save_run_state(ctx, state)


def _context_chat_id(ctx) -> str:
    """Isolation bucket for shared IM sessions: external chat_id when present, else '' (web/legacy)."""
    mm = ctx.message_meta or {}
    source = (mm.get("source") or "")
    if not source.startswith("im:"):
        return ""
    return (mm.get("chat_id") or "").strip()


def _row_chat_id(row) -> str:
    """Extract the chat_id stored in a ChatMessage.meta JSON, default ''."""
    try:
        meta = json.loads(row.meta or "{}")
    except Exception:
        return ""
    if not isinstance(meta, dict):
        return ""
    return (meta.get("chat_id") or "").strip()


def _load_recent_history(ctx) -> list[tuple[str, str]]:
    """Load recent user/assistant turns (history_length rounds) as (role, content).

    IM providers share one web session; filter to the current external chat so
    different users/chats don't contaminate each other's LLM context.
    """
    from app.models import ChatMessage

    n_rounds = max(1, int(getattr(ctx.agent, "history_length", None) or 10))
    max_msgs = n_rounds * 2
    chat_id = _context_chat_id(ctx)
    try:
        rows = (
            ctx.db.query(ChatMessage)
            .filter(
                ChatMessage.agent_id == ctx.agent.id,
                ChatMessage.session_id == ctx.session_id,
                ChatMessage.role.in_(["user", "assistant"]),
            )
            .order_by(ChatMessage.id.desc())
            .limit(max_msgs * 4 + 1)   # wider window so filtering other chats doesn't starve history
            .all()
        )
    except Exception:
        return []
    rows = list(reversed(rows))          # 升序
    if rows and rows[-1].role == "user":  # 丢弃 run() 已落盘的当前用户消息
        rows = rows[:-1]
    if chat_id:
        rows = [r for r in rows if _row_chat_id(r) == chat_id]
    rows = rows[-max_msgs:]
    out: list[tuple[str, str]] = []
    for r in rows:
        content = (r.content or "").strip()
        if not content:
            continue
        if r.role == "assistant" and len(content) > 1200:  # 上下文卫生，非门禁
            content = content[:1200] + "\n…(已截断)"
        out.append((r.role, content))
    return out


_TOOL_MARKER_LINE = re.compile(rf"^\s*(?:{PROTOCOL_MARKERS})")
_WRITE_LINE = re.compile(r"^\s*WRITE\s*[:：]")


def _history_reply(text: str) -> str:
    """上下文卫生：history 层只保留协议骨架，正文交给 tool_result/archive。

    WRITE 的多行正文压缩为首行（正文已在 tool_result/archive 中），其余超长
    内容截断，避免 history 层无界膨胀。不改变用于解析的 reply 本体。
    """
    t = text or ""
    out: list[str] = []
    skip_body = False
    for ln in t.split("\n"):
        if skip_body and not _TOOL_MARKER_LINE.match(ln):
            continue
        skip_body = _WRITE_LINE.match(ln) is not None
        out.append(ln)
    t = "\n".join(out)
    if len(t) > 2000:
        t = t[:2000] + "\n…(已截断)"
    return t


def _load_session_summary(ctx) -> str:
    """Return the rolling session summary text (empty if none)."""
    try:
        from app.models import ChatSummary

        s = ctx.db.query(ChatSummary).filter(
            ChatSummary.agent_id == ctx.agent.id,
            ChatSummary.session_id == ctx.session_id,
            ChatSummary.chat_id == _context_chat_id(ctx),
        ).first()
        return (s.content or "").strip() if s else ""
    except Exception:
        return ""


def _tool_dead_end(text: str) -> bool:
    """True when a tool result is a dead-end (not an error, but no progress possible)."""
    t = (text or "").strip()
    if not t:
        return False
    return any(k in t for k in (
        "文件不存在", "无相关结果", "no mcp configured",
        "skill not found", "skill not matched", "非法路径", "非法写入路径",
    ))


def _tool_result_failed(text: str) -> bool:
    """True when a tool result string indicates a failure or dead-end."""
    t = (text or "").strip()
    return t.startswith((
        "MCP 错误", "MCP 调用失败", "MCP URL 未配置", "MCP 无响应",
        "工具执行异常", "[exit ",
    )) or _tool_dead_end(t)


def _is_mcp_connect_failure(text: str) -> bool:
    """True when an MCP call failed at the transport/exception level (not a
    tool-specific business error like "Unknown tool").

    ``_format_mcp_call_failure`` in mcp_client emits "MCP 调用失败:" for
    connect/timeout/exception failures — i.e. the endpoint (or the whole MCP
    connection) is broken, so the failure applies to every tool on it, not just
    the one tool name that was attempted.
    """
    return (text or "").strip().startswith("MCP 调用失败")


def _is_cached_reference(text: str) -> bool:
    """True when an MCP result is the dedup-hit reference emitted by
    ``McpSessionManager._cached_reference`` (a soft "已缓存/已落盘，请 READ" hint)."""
    return bool(text) and text.startswith("该结果已缓存/已落盘")


def _cached_reference_path(text: str) -> str:
    """Extract the materialized path from a cached-reference message."""
    if not _is_cached_reference(text):
        return ""
    patterns = (
        r"已缓存/已落盘\s+([^\s，。；;]+)",
        r"读取\s+([^\s，。；;]+)",
    )
    for pattern in patterns:
        m = re.search(pattern, text or "")
        if m:
            return m.group(1).strip()
    return ""


def _cached_reference_recovery_hint(text: str, allowed_actions: list[str]) -> str:
    """High-priority recovery instruction after a cached MCP hit.

    Weak ReAct models often read the prose "勿重跑" but still repeat the MCP call.
    Give them one exact executable line for the next round while preserving the
    hard-stop if they ignore it.
    """
    path = _cached_reference_path(text)
    if not path:
        return "【缓存命中】上一个 MCP 结果已落盘。不要重复调用同一 MCP；请改用 READ/SHELL 读取缓存文件，或基于已有结果输出 FINAL。"
    allowed = set(allowed_actions or [])
    if "file_read" in allowed:
        action = f"READ: {path}"
    elif "shell" in allowed:
        action = f"SHELL: cat {path}"
    else:
        action = "FINAL: <基于已有缓存结果说明当前进度/缺口>"
    return (
        "【缓存命中·必须换路】上一个 MCP 查询已经命中缓存/落盘。"
        "不要再次调用同一个 MCP tool+args；下一轮请直接输出下面这一行可执行指令：\n"
        f"{action}"
    )


# react-engine-v10 R2/R3: READ/SHELL/SEARCH result dedup (mirrors MCP query_cache).
_DEDUP_TOOL_LABEL = {"file_read": "READ", "shell": "SHELL", "file_search": "SEARCH"}


def _dedup_entry(desc: dict, action: str, text: str) -> dict:
    """Cache entry for a READ/SHELL/SEARCH result (shape matches MCP query_cache)."""
    return {
        "path": desc.get("rel"),
        "tool": action,
        "size": len(text or ""),
        "mtime": desc.get("mtime"),
    }


def _dedup_entry_valid(desc: dict, entry: dict) -> bool:
    """False when a cached READ target's mtime has changed since it was recorded."""
    if desc.get("mtime") is not None and entry.get("mtime") is not None:
        if desc["mtime"] != entry["mtime"]:
            return False
    return True


def _dedup_hit_pointer(action: str, entry: dict) -> str:
    """Soft "already cached, reuse the earlier result" reference (R2)."""
    label = _DEDUP_TOOL_LABEL.get(action, action)
    size = entry.get("size", 0)
    return (
        f"该结果已缓存，请引用之前 {label} 的结果（{size} 字符）；"
        "勿重复执行相同动作，直接复用上次观察即可。"
    )


_REFLECT_DELIVERABLE_LINES = 20


def _deliverable_evidence(paths, sandbox, *, max_lines: int = 20, max_chars: int = 1500) -> str:
    """Head-of-file summaries for saved deliverables (R4).

    Lets the Verifier check real numbers/SQL/field semantics against file content
    instead of guessing from candidate text. Pure file read — no tool call, no LLM.
    """
    from app.services.workplace import workplace_root

    if not paths:
        return "（无）"
    sid = sandbox.id if sandbox else "default"
    blocks: list[str] = []
    budget = max_chars
    for p in paths:
        if budget <= 0:
            break
        try:
            wp = workplace_root(sid) / p.lstrip("/")
            if not wp.is_file():
                continue
            head = "\n".join(
                wp.read_text(encoding="utf-8", errors="replace").splitlines()[:max_lines]
            )
        except Exception:
            continue
        head = head[:budget]
        if not head:
            continue
        blocks.append(f"- {p}:\n{head}")
        budget -= len(head) + len(p) + 16
    return "\n".join(blocks) if blocks else "（无）"


_MISSING_MODULE_RE = re.compile(r"ModuleNotFoundError|No module named|ImportError", re.I)


def _missing_dep_hint(action: str, text: str) -> str | None:
    """SHELL 缺依赖（ModuleNotFoundError）时给出一键自装提示，避免空转等满 5 轮才收到通用失败提示。"""
    t = (text or "").strip()
    if action != "shell" or not _MISSING_MODULE_RE.search(t):
        return None
    m = re.search(r"No module named ['\"]?([\w.]+)", t)
    pkg = (m.group(1) if m else "").split(".")[0] or "pandas"
    return (
        f"【依赖缺失】SHELL 报错缺少 `{pkg}`。请先执行一次 "
        f"`SHELL: pip install -i https://mirrors.aliyun.com/pypi/simple/ {pkg}` 再继续；"
        "装好后直接复用，不要每轮重复安装。"
    )


def _final_text(raw: str) -> str:
    """User-facing final reply; never empty for non-empty input.

    `clean_final_answer` empties a reply that only contains protocol markers
    (a bare `FINAL:` with no payload, or a reply of pure tool-call lines).
    Fall back to the artifact-stripped raw text so the user sees what the
    agent actually emitted instead of an empty-reply placeholder.
    """
    if not raw or not raw.strip():
        return ""
    from app.services.tool_parser import clean_final_answer, _strip_llm_artifacts

    cleaned = clean_final_answer(raw)
    if cleaned:
        return cleaned
    return _strip_llm_artifacts(raw).strip()


def _is_duplicate_reply(prev: str, cur: str) -> bool:
    """退化死循环签名：连续两轮纯文本几乎相同（如 "blocked" / "--blocked--"）。"""
    a = re.sub(r"\s+", "", (prev or "")).lower()
    b = re.sub(r"\s+", "", (cur or "")).lower()
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) > 40 and len(b) > 40 and a[:600] == b[:600]:
        return True
    # 短回复且互为子串，覆盖 "blocked" vs "tool_blocked"/"--blocked--" 等变体
    return len(a) <= 40 and len(b) <= 40 and (a in b or b in a)


# READ/SHELL oversized-result dump threshold (react-engine-v9 R2). Mirrors MCP's
# mcp_result_*.json materialization: full text lands in task/<run_ts>/, context
# only echoes path + preview + length.
_LARGE_RESULT_CHARS = 4000
_LARGE_RESULT_PREVIEW_LINES = 10


def _materialize_tool_result(
    action: str,
    text: str,
    *,
    run_ts: str,
    sandbox,
    seq: int,
    threshold: int = _LARGE_RESULT_CHARS,
) -> tuple[str, str | None]:
    """Dump an oversized READ/SHELL result to task/<run_ts>/ and echo a preview.

    Returns ``(context_text, dumped_path)``. When the action is not READ/SHELL, or
    the text is under the threshold, ``dumped_path`` is ``None`` and
    ``context_text`` is the original text unchanged. ``dumped_path`` is the path
    written (relative to the workplace); the caller uses its presence to advance
    the sequence counter. SEARCH is excluded — its result is already capped.
    """
    if action not in ("file_read", "shell") or not text or len(text) <= threshold:
        return text, None
    from app.services.workplace import workplace_root

    sid = sandbox.id if sandbox else "default"
    rel = f"task/{run_ts}/{action}_result_{seq}.txt"
    try:
        wp = workplace_root(sid) / rel
        wp.parent.mkdir(parents=True, exist_ok=True)
        wp.write_text(text, encoding="utf-8")
    except Exception:
        return text, None
    preview_lines = text.splitlines()[:_LARGE_RESULT_PREVIEW_LINES]
    preview = "\n".join(preview_lines)[:1200]
    echo = (
        f"结果过大（{len(text)} 字符），已全量写入 {rel}。\n"
        f"前 {len(preview_lines)} 行预览：\n{preview}\n"
        "…（后续内容请用 READ 或 SEARCH 按需取回，勿在本轮上下文粘贴原始数据）"
    )
    return echo, rel


def _build_stuck_hint(dup_text: str, state) -> str:
    """软性卡死提示：点名重复 + 回放已完成进度 + 给两条逃生路径。"""
    progress = [ln for ln in state.progress_lines if ln.strip()][-8:]
    prog = "\n".join(f"- {ln}" for ln in progress) if progress else "（暂无）"
    return (
        "【卡死检测】你已连续多轮输出几乎相同的文字（如「" + (dup_text or "")[:30] + "」），任务没有推进。请立即二选一：\n"
        "1) 调用工具（无依赖可同轮多个）继续推进（例如 SHELL 读取 task/ 下已落盘的数据生成 xlsx，或 READ 查看文件结构）；\n"
        "2) 输出一行 `FINAL: <当前进度/结果>` 结束本轮。\n"
        "本轮已完成：\n" + prog
    )


def _tool_fail_hint(action: str, args: str, streak: int, circuit: int) -> str | None:
    """软性失败熔断提示：同一工具连续失败达到阈值时给出二选一逃生路径。"""
    if streak < circuit:
        return None
    return (
        f"【提示】工具 `{action}` 已连续失败 {streak} 次（{(args or '')[:40]}）。"
        "请更换工具或修正参数、改用其它数据源，或输出 FINAL 说明当前缺口。"
    )


def _tool_repeat_hint(action: str, args: str, streak: int) -> str:
    """软性无进展提示：同一工具+参数连续成功但结果无变化时，引导换路或 FINAL。"""
    return (
        f"【无进展检测】工具 `{action}`（{(args or '')[:40]}）已连续调用 {streak} 次且结果无变化。"
        "请改用其它工具/参数推进，或输出 FINAL 说明当前进度。"
    )


def _distill_hint(tool: str, count: int) -> str:
    """软性映射蒸馏提示：同一 MCP 工具被大量不同参数调用（如逐个 describe 视图）。

    原始 describe/query 结果会被截断或落盘，模型若不停下来把「view→字段/口径」
    映射蒸馏进 PLAN 或落盘文件，就会陷入重试/回写循环。纯 coach hint，不强制停止。
    """
    return (
        f"【映射蒸馏】你已 {count} 次调用 MCP 工具 `{tool}`（多为不同参数）。"
        "原始 describe/查询结果会被截断或落盘，请把已确认的「view→字段/口径」映射蒸馏进 PLAN"
        "（或落盘 task/<ts>/view_map.json），然后进入下一子任务；"
        "不要逐个 describe 大量资源——先按字段名/口径定位候选，再 describe 确认。"
    )


def _tool_step_title(action: str, normalized: str) -> str:
    """Compact, user-safe step title: the real command/tool plus a short arg preview.

    Replaces the bare ``[shell]`` / ``[mcp_tool_call]`` so the UI shows *what* ran.
    """
    body = (normalized or "").strip()
    if action == "shell" and body.startswith("SHELL:"):
        body = body[6:].strip()
    elif action == "mcp_tool_call":
        m = re.match(r"MCP:\s*(\S+)\s*(.*)", body, re.DOTALL)
        if m:
            body = f"{m.group(1)} {m.group(2).strip()}".strip()
    elif action == "file_write":
        m = re.match(r"WRITE:\s*(\S+)", body)
        body = m.group(1) if m else body
    elif action == "file_read":
        body = re.sub(r"^READ:\s*", "", body)
    else:
        body = re.sub(r"^[A-Z_]+:\s*", "", body).split("\n", 1)[0]
    body = " ".join(body.split())[:120]
    return f"[{action}] {body}".strip() if body else f"[{action}]"


_RAW_CODE_START_RE = re.compile(
    r"^\s*(?:import\s+\w+|from\s+\w+\s+import|for\s+\w+\s+in\s|def\s+\w+|while\s+"
    r"|print\s*\(|echo\s+|cat\s+|python3?\s+|#!/)",
    re.IGNORECASE,
)

# 裸 Python 赋值/下标/方法链片段（如 df=df.merge(...)、ch['id']=...），
# 漏写协议前缀且不可独立运行（变量来自上一轮进程），只用于 coach hint，不自动执行。
_BARE_PYTHON_RE = re.compile(
    r"^\s*[A-Za-z_]\w*\s*(?:=|\[)|"
    r"^\s*[A-Za-z_]\w*\s*\.\s*[A-Za-z_]\w*\s*\("
)


def _looks_like_leaked_tool_call(text: str) -> bool:
    """模型漏出原生 tool_call 特殊令牌（如 MiniMax 的 <|tool_call|>）而非协议行。"""
    return bool(LEAKED_TOOL_TOKEN_RE.search(text or ""))


def _looks_like_bare_python(text: str) -> bool:
    """裸 Python 数据操作片段（赋值/下标/方法链），非独立可运行脚本。"""
    return bool(_BARE_PYTHON_RE.match((text or "").strip()))


def _looks_like_raw_code(text: str) -> bool:
    """裸代码（未带 SHELL:/WRITE: 前缀），解析器不会当作工具执行。"""
    t = (text or "").strip()
    return bool(_RAW_CODE_START_RE.match(t) or _BARE_PYTHON_RE.match(t))


def _build_format_hint(text: str, *, bare_python: bool = False) -> str:
    """引导模型把漏出的 tool_call 令牌 / 裸代码改回引擎协议格式。

    ``bare_python`` 针对不可独立运行的 Python 数据片段（变量来自上一轮进程），
    强调 SHELL 无状态 + 写完整脚本到 /tmp/ 再执行。
    """
    sample = re.sub(r"\s+", " ", (text or "").strip())[:80]
    if bare_python:
        return (
            "【格式修正】你直接输出了 Python 数据操作片段（如 `" + sample + "`），但没有用协议前缀，"
            "这段内容不会被执行。注意：每次 SHELL 都是独立进程，上一轮的变量（df/bk/ch/gm 等）不会保留。\n"
            "正确做法是把**完整脚本**写到 /tmp/ 再执行：\n"
            "1) `WRITE: /tmp/build.py`\n"
            "    import pandas as pd, json\n"
            "    df = pd.DataFrame(json.load(open('task/<ts>/mcp_result_1.json'))['rows'])\n"
            "    … 全部 merge/astype/rename 操作 …\n"
            "    df.to_excel('报表.xlsx', index=False)\n"
            "2) `SHELL: python3 /tmp/build.py`\n"
            "完成后输出一行 `FINAL: <结论>`。"
        )
    return (
        "【格式修正】你刚才输出了 `<|tool_call|>` 或直接写了代码，但没有用引擎要求的协议前缀，"
        "这段内容不会被当作工具执行。请改用：\n"
        "1) 跑 Python：先 `WRITE: /tmp/build.py` 写入脚本，再 `SHELL: python3 /tmp/build.py`；"
        "或 `SHELL: python3 -c \"...\"`；\n"
        "2) 查数据：`MCP: <工具名> {\"args\"}`；\n"
        "3) 完成后输出一行 `FINAL: <结论>`。\n"
        f"（你刚才输出：{sample}）"
    )


_PYTHON_START_RE = re.compile(
    r"^\s*(?:"
    r"import\s+\w+|from\s+\w+\s+import\b|for\s+\w+\s+in\b|def\s+\w+\s*\("
    r"|while\s+.+:|class\s+\w+\b|with\s+open\s*\(|try\s*:|print\s*\(|"
    r"if\s+__name__"
    r")",
    re.IGNORECASE,
)

_SHELL_CMD_RE = re.compile(
    r"^\s*(?:"
    r"ls\b|cat\b|find\b|grep\b|cd\b|mkdir\b|echo\b|head\b|tail\b|wc\b|pwd\b|"
    r"python3?\b|sed\b|awk\b|mv\b|cp\b|tar\b|unzip\b|curl\b|wget\b|pip3?\b|"
    r"node\b|npm\b|which\b|file\b|du\b|df\b|touch\b|chmod\b|printf\b|env\b|"
    r"export\b|source\b|sort\b|uniq\b|cut\b|tr\b|tee\b|xargs\b|base64\b|jq\b|"
    r"zip\b|gzip\b|zcat\b|diff\b|ln\b|sleep\b|date\b|\./|\.\./"
    r")",
    re.IGNORECASE,
)

# Single-line blocks also need a "technical" signal (flag/pipe/glob/path/extension)
# so prose like "find the answer" isn't mis-executed as a shell command.
_SHELL_TECH_SIGNAL_RE = re.compile(r"[-*?<>|&;./]|2>&1|\.py\b|\.json\b|\.csv\b|\.xlsx\b|\.txt\b")


def _rescue_leaked_code(text: str) -> str | None:
    """Convert leaked bare shell / Python (missing protocol prefix) into an executable
    shell payload. Returns None when the text isn't confidently executable."""
    from app.services.tool_parser import _strip_reasoning_blocks

    raw = _strip_reasoning_blocks(text or "")
    raw = strip_leaked_tool_tokens(raw)
    raw = re.sub(r"(?im)^\s*EOF\s*$", "", raw).strip()
    if not raw:
        return None

    if _PYTHON_START_RE.match(raw):
        return _wrap_python_script(raw)

    # 裸 Python 赋值/方法链片段（如 df=...，会与 shell 的 df 命令撞名）不是 shell，
    # 也不可独立运行（变量来自上一轮进程），交给 coach hint 而非自动执行。
    if _BARE_PYTHON_RE.match(raw):
        return None

    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if not lines or not all(_SHELL_CMD_RE.match(ln) for ln in lines):
        return None
    if len(lines) == 1 and not _SHELL_TECH_SIGNAL_RE.search(lines[0]):
        return None
    return "\n".join(lines)


def _wrap_python_script(code: str) -> str:
    """Run leaked bare Python via a temp file (base64 sidesteps shell quoting)."""
    b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
    return (
        "mkdir -p /tmp/agent_rescue && "
        f"printf '%s' '{b64}' | base64 -d > /tmp/agent_rescue/rescue.py && "
        "python3 /tmp/agent_rescue/rescue.py"
    )


def _budget_near_hint(remaining: int) -> str | None:
    """预算临近软提示：剩余轮次少时提醒模型主动 FINAL 收尾。"""
    if remaining not in (5, 2):
        return None
    return (
        f"【预算告急】本轮迭代预算即将用完（剩余约 {remaining} 轮）。请立即二选一：\n"
        "1) 若已有中间产物（字段映射、已拉取数据），先把它们落盘到 task/ 或最终文件，"
        "再输出一行 `FINAL: <当前进度/缺口>` 结束本轮；\n"
        "2) 直接输出 `FINAL: <当前进度/缺口>`，已落盘内容会在断点续跑时保留。\n"
        "不要继续发起新的长查询，避免到达轮次上限被截断、中间结果丢失。"
    )


def _forced_stop_reply(state, reason: str) -> str:
    """Honest fallback when the loop exits without a FINAL (e.g. max_iters exhausted).

    Reports the reason plus actual progress, never echoing a possibly-degraded
    last_reply as "done". Progress and saved files are explicitly marked as
    intermediate artifacts, not final deliverables.
    """
    progress = [ln for ln in state.progress_lines if ln.strip()]
    if progress or state.saved_paths:
        lines = [f"任务未完成，已自动结束（{reason}）。"]
        if progress:
            lines.append("已产生的中间产物：\n" + "\n".join(f"- {ln}" for ln in progress[-12:]))
        if state.saved_paths:
            lines.append("工作区文件（非最终交付）：\n" + "\n".join(f"- {p}" for p in state.saved_paths[-8:]))
        return "\n".join(lines)
    cleaned = _final_text(state.last_reply)
    if cleaned:
        return (
            f"任务未完成，已自动结束（{reason}），未输出最终结论。\n\n"
            f"本轮最后的原始输出：\n{cleaned}"
        )
    return f"任务未完成，已自动结束（{reason}），请检查结果。"


def _archive_type_for(action: str) -> str:
    """Map a tool action to its archive category (full content, out-of-context)."""
    if action == "shell":
        return "log"
    if action == "file_write":
        return "code"
    return "tool_result"


# ---- Drop-in replacement for run_react_loop ----


async def run_agent(
    db,
    agent,
    session_id: str,
    user_message: str,
    workplace_dir: str = "",
    message_meta: dict | None = None,
    code_run_id: str | None = None,
    workspace_manager=None,
    code_runner=None,
    code_verifier=None,
    code_artifact_sealer=None,
) -> str:
    """Run one Agent under an outer compensation guard for Code startup."""
    guarded_run = None
    if (getattr(agent, "profile", None) or "standard") == "code" and code_run_id:
        from app.models import CodeAgentRun

        candidate = db.query(CodeAgentRun).filter(CodeAgentRun.id == code_run_id).first()
        if candidate and candidate.agent_id == agent.id and candidate.status == "pending":
            guarded_run = candidate
    try:
        return await _run_agent_impl(
            db,
            agent,
            session_id,
            user_message,
            workplace_dir=workplace_dir,
            message_meta=message_meta,
            code_run_id=code_run_id,
            workspace_manager=workspace_manager,
            code_runner=code_runner,
            code_verifier=code_verifier,
            code_artifact_sealer=code_artifact_sealer,
        )
    except BaseException:
        if guarded_run is not None and guarded_run.status == "pending":
            from app.services.code_agent.lifecycle import (
                CodeCleanupError,
                cleanup_code_resources,
            )

            try:
                await cleanup_code_resources(
                    f"{agent.id}:{session_id}",
                    guarded_run.id,
                    db=db,
                    terminal_status="infrastructure_error",
                    failure_reason="startup_failed",
                )
            except CodeCleanupError:
                guarded_run.status = "infrastructure_error"
                guarded_run.failure_reason = "cleanup_failed"
                db.commit()
                raise
        raise


async def _publish_pre_context_code_step(
    agent,
    session_id: str,
    *,
    action: str,
    title: str,
    status: str,
    content: str = "",
    op: str = "append",
) -> None:
    """Publish visible Code startup steps before AgentContext exists."""
    from app.services.agent_runtime.hub import hub

    step = {
        "type": "info",
        "action": action,
        "title": title,
        "status": status,
    }
    if content:
        step["content"] = content[:400]
    try:
        await hub.publish(
            f"{agent.id}:{session_id}",
            {"type": "step", "op": op, "step": step},
        )
    except Exception:
        logger.exception("code startup step publish failed action=%s", action)


async def _publish_pre_context_code_runtime_event(
    agent,
    session_id: str,
    *,
    event: dict,
    db=None,
) -> None:
    """Publish a normalized Claude Code runtime event before AgentContext exists."""
    from app.services.agent_runtime.hub import hub

    _persist_code_profile_event(db, str(event.get("run_id") or ""), event)
    try:
        await hub.publish(
            f"{agent.id}:{session_id}",
            {"type": "profile", "agent_id": agent.id, "session_id": session_id, "profile": event},
        )
    except Exception:
        logger.exception(
            "code runtime event publish failed phase=%s", event.get("phase")
        )


async def _run_agent_impl(
    db,
    agent,
    session_id: str,
    user_message: str,
    workplace_dir: str = "",
    message_meta: dict | None = None,
    code_run_id: str | None = None,
    workspace_manager=None,
    code_runner=None,
    code_verifier=None,
    code_artifact_sealer=None,
) -> str:
    """Resolve agent config from the DB, build an AgentContext, run the loop."""
    from app.models import CodeAgentRun, LLMResource, ModelRoutingPolicy, Sandbox, ChatNote, User
    from app.services.agent_runtime.context import AgentContext, CodeExecutionContext
    from app.services.agent_runtime.utils import _bound_mcp_names
    from app.services.skill_loader import load_skill_mds

    profile = getattr(agent, "profile", None) or "standard"
    if profile == "code" and getattr(agent, "routing_policy_id", ""):
        raise ValueError("routing_policy_not_supported_for_code_profile")
    code_execution = None
    sandbox = None
    allowed = json.loads(agent.allowed_actions or "[]")
    skill_ids = json.loads(agent.skills or "[]")
    mcp_ids = json.loads(agent.mcps or "[]")
    httpmcp_ids = json.loads(getattr(agent, "httpmcps", None) or "[]")
    rag_ids = json.loads(getattr(agent, "rags", None) or "[]")

    # Auto-include required action types when corresponding bindings exist.
    if mcp_ids and "mcp_tool_call" not in allowed:
        allowed.append("mcp_tool_call")
    if httpmcp_ids and "httpmcp_call" not in allowed:
        allowed.append("httpmcp_call")
    if skill_ids:
        if "skill_read_md" not in allowed:
            allowed.append("skill_read_md")
        if "skill_run_script" not in allowed:
            allowed.append("skill_run_script")

    if profile == "code":
        if not code_run_id:
            from app.services.code_agent.control_plane import agent_would_use_claude_code

            if not agent_would_use_claude_code(db, agent):
                raise ValueError("code_run_required")
        if code_run_id:
            code_run = db.query(CodeAgentRun).filter(CodeAgentRun.id == code_run_id).first()
            if not code_run or code_run.agent_id != agent.id:
                raise ValueError("code_run_unavailable")
            if code_run.status != "pending":
                raise ValueError("code_run_not_pending")
            # Revalidate sealed source evidence before allocating any writable
            # workspace or starting a runner. A failed scan must be terminal
            # for this run and must not leave partially created resources.
            from app.services.code_agent.scanner import (
                SourceScanValidationError,
                validate_source_scan_report,
            )
            try:
                validate_source_scan_report(db, code_run)
            except SourceScanValidationError as exc:
                code_run.status = "policy_rejected"
                code_run.failure_reason = exc.reason
                db.commit()
                raise
            chat_key = f"{agent.id}:{session_id}"
            from app.services.code_agent.lifecycle import bind_code_run, register_code_cleanup
            sandbox = db.query(Sandbox).filter(Sandbox.id == agent.sandbox_id).first()
            if workspace_manager is None:
                from app.services.code_agent.workspace import WorkspaceManager
                workspace_manager = WorkspaceManager()
            workspace_cleanup = getattr(
                workspace_manager, "cleanup_allocated_workspace", None
            )
            await _publish_pre_context_code_step(
                agent,
                session_id,
                action="code_workspace_prepare",
                title="准备 Code Workspace",
                status="running",
            )
            workspace = workspace_manager.prepare(
                code_run,
                sandbox_id=str(getattr(agent, "sandbox_id", "") or ""),
            )
            if callable(workspace_cleanup):
                register_code_cleanup(
                    code_run.id, lambda: workspace_cleanup(code_run)
                )
            await _publish_pre_context_code_step(
                agent,
                session_id,
                action="code_workspace_prepare",
                title="准备 Code Workspace",
                status="done",
                op="patch",
            )
            if not callable(workspace_cleanup) and hasattr(
                workspace_manager, "retain_after_run"
            ):
                register_code_cleanup(
                    code_run.id, lambda: workspace_manager.retain_after_run(code_run)
                )
            if code_runner is None:
                from app.services.code_agent.runner import CodeContainerRunner
                code_runner = CodeContainerRunner()
            try:
                await _publish_pre_context_code_step(
                    agent,
                    session_id,
                    action="code_runner_start",
                    title="启动 Code Sandbox",
                    status="running",
                )
                runner_facts = code_runner.start(code_run, workspace, sandbox=sandbox)
            except Exception as exc:
                from app.services.code_agent.failures import record_code_failure

                await _publish_pre_context_code_step(
                    agent,
                    session_id,
                    action="code_runner_start",
                    title="启动 Code Sandbox 失败",
                    status="error",
                    content=str(getattr(exc, "reason", "") or "startup_failed"),
                    op="patch",
                )
                try:
                    record_code_failure(
                        db,
                        actor=getattr(agent, "name", "") or getattr(agent, "id", "") or "agent",
                        reason=getattr(exc, "reason", "startup_failed") or "startup_failed",
                        project_id=code_run.project_id,
                        run_id=code_run.id,
                        policy_hash=code_run.effective_policy_hash,
                        details={
                            "operation": "runner:start",
                            "image": code_run.image,
                            "sandbox_id": getattr(sandbox, "id", "") if sandbox else "",
                            "error_type": type(exc).__name__,
                            "error_summary": (
                                getattr(exc, "detail", "")
                                or str(getattr(exc, "reason", "") or exc)
                            ),
                        },
                    )
                except Exception:
                    logger.exception("Failed to record CodeAgent runner startup failure")
                raise
            await _publish_pre_context_code_step(
                agent,
                session_id,
                action="code_runner_start",
                title="启动 Code Sandbox",
                status="done",
                op="patch",
            )
            code_run.container_id = runner_facts.container_id
            code_run.execution_eligible = True
            code_run.runner_state = "active"
            code_run.runner_network_id = ""
            code_run.cleanup_state = "pending"
            from app.services.code_agent.workspace import (
                WorkspaceGitSyncError,
                sync_repository_in_sandbox,
            )

            try:
                await _publish_pre_context_code_step(
                    agent,
                    session_id,
                    action="code_repository_sync",
                    title="同步 Git 仓库",
                    status="running",
                )
                git_sync = sync_repository_in_sandbox(
                    db, code_run, code_runner, runner_facts
                )
                await _publish_pre_context_code_step(
                    agent,
                    session_id,
                    action="code_repository_sync",
                    title="同步 Git 仓库",
                    status="done",
                    content=git_sync.get("resolved_commit", ""),
                    op="patch",
                )
            except WorkspaceGitSyncError as exc:
                from app.services.code_agent.failures import record_code_failure

                code_run.status = "infrastructure_error"
                code_run.failure_reason = exc.reason
                db.commit()
                await _publish_pre_context_code_step(
                    agent,
                    session_id,
                    action="code_repository_sync",
                    title="同步 Git 仓库失败",
                    status="error",
                    content=exc.reason,
                    op="patch",
                )
                try:
                    record_code_failure(
                        db,
                        actor=getattr(agent, "name", "") or getattr(agent, "id", "") or "agent",
                        reason=exc.reason,
                        project_id=code_run.project_id,
                        run_id=code_run.id,
                        policy_hash=code_run.effective_policy_hash,
                        details={
                            "operation": "repository:sync",
                            "error_type": type(exc).__name__,
                            "error_summary": exc.detail[:300],
                        },
                    )
                except Exception:
                    logger.exception("Failed to record CodeAgent repository sync failure")
                raise
            # Materialize the Claude SOP after repository sync so repo-local
            # instructions and the bound Sandbox see the same Workspace tree.
            from app.services.code_agent.claude_code_runtime import (
                code_run_uses_claude_code,
                materialize_coding_sop,
            )
            if code_run_uses_claude_code(code_run):
                materialize_coding_sop(workspace.path)
            from app.services.code_agent.claude_code_runtime import (
                code_run_uses_claude_code,
                materialize_claude_code_skills,
                materialize_claude_code_mcp_config,
                normalize_claude_code_runtime_event,
                preflight_input_from_run,
                record_claude_code_mcp_injection,
                record_claude_code_skill_injection,
                record_claude_code_preflight,
                run_claude_code_preflight,
            )

            if code_run_uses_claude_code(code_run):
                await _publish_pre_context_code_runtime_event(
                    agent,
                    session_id,
                    db=db,
                    event=normalize_claude_code_runtime_event(
                        {
                            "type": "runtime_started",
                            "runtime": "claude_code",
                            "status": "started",
                        },
                        run_id=code_run.id,
                    ),
                )
                skill_injection = materialize_claude_code_skills(
                    db, code_run, workspace.path
                )
                record_claude_code_skill_injection(code_run, skill_injection)
                for event in skill_injection.events:
                    await _publish_pre_context_code_runtime_event(
                        agent,
                        session_id,
                        db=db,
                        event=normalize_claude_code_runtime_event(event, run_id=code_run.id),
                    )
                mcp_injection = materialize_claude_code_mcp_config(
                    db, code_run, workspace.path
                )
                record_claude_code_mcp_injection(code_run, mcp_injection)
                for event in mcp_injection.events:
                    await _publish_pre_context_code_runtime_event(
                        agent,
                        session_id,
                        db=db,
                        event=normalize_claude_code_runtime_event(event, run_id=code_run.id),
                    )
                await _publish_pre_context_code_step(
                    agent,
                    session_id,
                    action="claude_code_preflight",
                    title="检查 Claude Code Runtime",
                    status="running",
                )
                preflight = run_claude_code_preflight(
                    preflight_input_from_run(
                        code_run,
                        runner_facts,
                        mcp_config_json=mcp_injection.config_json,
                    ),
                    runner=code_runner,
                )
                record_claude_code_preflight(code_run, preflight)
                if not preflight.passed:
                    code_run.status = preflight.failure_type
                    code_run.failure_reason = preflight.reason
                    db.commit()
                    await _publish_pre_context_code_step(
                        agent,
                        session_id,
                        action="claude_code_preflight",
                        title="检查 Claude Code Runtime 失败",
                        status="error",
                        content=preflight.reason,
                        op="patch",
                    )
                    raise RuntimeError(preflight.reason)
                await _publish_pre_context_code_step(
                    agent,
                    session_id,
                    action="claude_code_preflight",
                    title="检查 Claude Code Runtime",
                    status="done",
                    op="patch",
                )
            db.commit()
            bind_code_run(chat_key, code_run.id)
            policy = json.loads(code_run.effective_policy or "{}")
            budgets = policy.get("budgets") if isinstance(policy, dict) else {}
            timeout_seconds = int((budgets or {}).get("timeout_seconds") or 1800)
            code_execution = CodeExecutionContext(
                run_id=code_run.id,
                project_id=code_run.project_id,
                manifest_id=code_run.manifest_id,
                manifest_version=code_run.manifest_version,
                repository=code_run.repository,
                base_commit=code_run.base_commit,
                task_contract_json=code_run.task_contract,
                effective_policy_json=code_run.effective_policy,
                timeout_seconds=max(1, timeout_seconds),
                workspace_path=workspace.path,
                source_facts_json=code_run.source_facts,
                runner_facts_json=code_run.runner_facts,
                container_id=runner_facts.container_id,
                image=runner_facts.image,
            )
    elif code_run_id:
        raise ValueError("code_run_not_allowed_for_standard_profile")

    llm = db.query(LLMResource).filter(LLMResource.id == agent.llm_id).first()
    model_route_decision_id = ""
    model_route_policy_id = ""
    model_route_policy_version = 0
    model_route_fallbacks = []
    model_route_duration_ms = 0
    if profile == "standard" and getattr(agent, "routing_policy_id", ""):
        policy = db.get(ModelRoutingPolicy, agent.routing_policy_id)
        owner = db.query(User).filter(User.username == agent.creator).first()
        if policy is not None and owner is not None:
            from app.services.model_router import (
                build_ordered_fallback_candidates,
                build_route_candidates,
                persist_route_decision,
                required_modalities_for_message,
                route_model,
            )
            required_modalities = required_modalities_for_message(message_meta)
            built = build_route_candidates(
                db, policy, owner, modalities=required_modalities,
            )
            route_started = time.monotonic()
            selected = await route_model(db, policy, user_message, built.candidates, timeout=getattr(agent, "llm_timeout", None) or 60)
            model_route_duration_ms = int((time.monotonic() - route_started) * 1000)
            decision = persist_route_decision(db, agent_id=agent.id, session_id=session_id, policy=policy, selection=selected, candidates=built.candidates, exclusions=built.exclusions)
            if decision is not None:
                llm = db.get(LLMResource, decision.llm_id)
                model_route_decision_id = decision.id
                model_route_policy_id = policy.id
                model_route_policy_version = policy.version
                model_route_fallbacks = build_ordered_fallback_candidates(
                    db, policy, owner, selected.candidate,
                    modalities=required_modalities,
                )
    if sandbox is None:
        sandbox = db.query(Sandbox).filter(Sandbox.id == agent.sandbox_id).first()

    tool_executor = None
    if code_execution:
        # Code permissions come exclusively from the frozen Code policy, never from
        # a reusable Agent's pre-existing generic shell/MCP/RAG grants. Skills are
        # retained as context capabilities so code-specific project knowledge loads.
        capability_actions = {
            "read": "code_read", "search": "code_search",
            "edit": "code_edit", "test": "code_test",
            "shell": "code_shell", "git_read": "code_git",
        }
        policy = json.loads(code_run.effective_policy or "{}")
        allowed = [
            capability_actions[item]
            for item in policy.get("allowed_tools") or []
            if item in capability_actions
        ]
        if skill_ids:
            allowed.extend([
                action
                for action in ("skill_read_md", "skill_run_script")
                if action not in allowed
            ])
        mcp_ids, httpmcp_ids, rag_ids = [], [], []
        from app.services.code_agent.tools import CodeToolExecutor
        tool_executor = CodeToolExecutor(db, code_run, code_runner)

    save_dir = workplace_dir.strip().strip("/")

    note = db.query(ChatNote).filter(
        ChatNote.agent_id == agent.id, ChatNote.session_id == session_id
    ).first()
    note_content = note.content.strip() if (note and note.content and note.content.strip()) else ""

    skill_mds = load_skill_mds(db, skill_ids)
    skill_names = [n for n, _md in (skill_mds or []) if n]
    mcp_names = _bound_mcp_names(db, mcp_ids)

    ctx = AgentContext.from_params(
        db=db,
        agent=agent,
        session_id=session_id,
        user_message=user_message,
        message_meta=message_meta,
        llm=llm,
        model_route_decision_id=model_route_decision_id,
        model_route_policy_id=model_route_policy_id,
        model_route_policy_version=model_route_policy_version,
        model_route_duration_ms=model_route_duration_ms,
        model_route_fallbacks=model_route_fallbacks,
        sandbox=sandbox,
        allowed_actions=allowed,
        profile=profile,
        code_execution=code_execution,
        tool_executor=tool_executor,
        skill_ids=skill_ids,
        mcp_ids=mcp_ids,
        rag_ids=rag_ids,
        httpmcp_ids=httpmcp_ids,
        skill_mds=skill_mds,
        skill_names=skill_names,
        mcp_names=mcp_names,
        save_dir=save_dir,
        note_content=note_content,
    )

    runtime = AgentRuntime()
    if code_execution:
        final = await _run_code_runtime(
            db,
            runtime,
            ctx,
            code_verifier=code_verifier,
            code_artifact_sealer=code_artifact_sealer,
            workspace_manager=workspace_manager,
        )
    else:
        final = await runtime.run(ctx)
    try:
        from app.services.session_summary import generate_session_summary

        await generate_session_summary(db, agent, session_id, chat_id=_context_chat_id(ctx))
    except Exception:
        logger.warning(
            "auto rolling summary failed agent=%s session=%s",
            agent.id, session_id, exc_info=True,
        )
    return final


async def _run_code_runtime(
    db,
    runtime: AgentRuntime,
    ctx,
    *,
    code_verifier=None,
    code_artifact_sealer=None,
    workspace_manager=None,
) -> str:
    """Apply timeout, terminal status and resource cleanup around the shared runtime."""
    import asyncio
    import time

    from app.models import CodeAgentRun
    from app.services.agent_runtime.hub import _running
    from app.services.code_agent.lifecycle import (
        CodeCleanupError,
        cleanup_code_resources,
        code_termination_reason,
        request_code_termination,
    )

    execution = ctx.code_execution
    started_at = time.monotonic()
    run = db.query(CodeAgentRun).filter(CodeAgentRun.id == execution.run_id).first()
    if run:
        run.status = "running"
        db.commit()

    from app.services.code_agent.verifier import CodeVerifier
    from app.services.code_agent.workspace import WorkspaceIntegrityGuard

    verifier = code_verifier or CodeVerifier(db, run, ctx.tool_executor.runner)
    result = ""
    failure: BaseException | None = None
    outcome = "execution_completed"
    from app.services.code_agent.claude_code_runtime import code_run_uses_claude_code

    publish_code_done = bool(run and code_run_uses_claude_code(run))
    if publish_code_done:
        # The Claude branch bypasses AgentRuntime.run(), so persist the user
        # turn here as well as the final assistant result below.
        runtime._save_user_message(ctx)
    try:
        await runtime._publish_code_profile_event(ctx, "verify_baseline", "started")
        baseline = verifier.capture_baseline(workspace_manager)
        await runtime._publish_code_profile_event(
            ctx,
            "verify_baseline",
            "completed" if baseline.get("status") in {"passed", "broken"} else "failed",
            str(baseline.get("reason") or ""),
        )
        from app.services.code_agent.claude_code_runtime import (
            ClaudeCodeRuntimeAdapter,
            claude_code_max_verifier_retries,
            claude_code_verifier_feedback,
            normalize_claude_code_runtime_event,
            record_claude_code_runtime_result,
            resolve_claude_code_exec_env,
            runtime_input_from_execution,
        )

        if run and code_run_uses_claude_code(run):
            exec_env, env_reason = resolve_claude_code_exec_env(db, run)
            if env_reason:
                outcome = env_reason
                result = env_reason
            else:
                event_loop = asyncio.get_running_loop()

                def publish_stream_event(raw_event):
                    event = normalize_claude_code_runtime_event(raw_event, run_id=run.id)
                    future = asyncio.run_coroutine_threadsafe(
                        runtime._publish_code_profile_event(
                            ctx,
                            event["phase"],
                            event["status"],
                            event.get("reason", ""),
                            {
                                key: value
                                for key, value in event.items()
                                if key not in {
                                    "version", "profile", "phase", "status",
                                    "run_id", "manifest_version", "reason",
                                }
                            },
                        ),
                        event_loop,
                    )
                    try:
                        future.result(timeout=10)
                    except Exception:
                        logger.warning("Claude stream event publish failed", exc_info=True)

                adapter = ClaudeCodeRuntimeAdapter(
                    runner=ctx.tool_executor.runner,
                    on_event=publish_stream_event,
                )
                max_retries = claude_code_max_verifier_retries(run)
                retry_attempt = 0
                while True:
                    # Keep the shared live execution card active for the full
                    # Claude CLI call. The adapter streams tool events through
                    # the bound Runner while this call is still in progress.
                    await runtime._publish_code_profile_event(
                        ctx,
                        "runtime_result",
                        "started",
                        "Claude Code Runtime 执行中",
                        {"retry_attempt": retry_attempt},
                    )
                    coding_result = await asyncio.wait_for(
                        asyncio.to_thread(
                            adapter.run,
                            runtime_input_from_execution(
                                execution,
                                run,
                                retry_attempt=retry_attempt,
                                verifier_feedback=result if retry_attempt > 0 else "",
                                exec_env=exec_env,
                            ),
                        ),
                        timeout=execution.timeout_seconds,
                    )
                    record_claude_code_runtime_result(run, coding_result)
                    db.commit()
                    for raw_event in coding_result.runtime_events:
                        if str(raw_event.get("type") or "") == "runtime_started":
                            continue
                        event = normalize_claude_code_runtime_event(
                            raw_event, run_id=run.id
                        )
                        await runtime._publish_code_profile_event(
                            ctx,
                            event["phase"],
                            event["status"],
                            event.get("reason", ""),
                            {
                                key: value
                                for key, value in event.items()
                                if key not in {
                                    "version", "profile", "phase", "status",
                                    "run_id", "manifest_version", "reason",
                                }
                            },
                        )
                    result = coding_result.summary
                    await runtime._publish_code_profile_event(
                        ctx,
                        "runtime_result",
                        "completed" if coding_result.status == "coding_completed" else "failed",
                        coding_result.error_summary or coding_result.summary,
                    )
                    if coding_result.status != "coding_completed":
                        outcome = coding_result.status
                        break
                    from pathlib import Path

                    control_baseline = Path(run.workspace_path).resolve().parent / "control" / "baseline.json"
                    runtime_changed = (
                        WorkspaceIntegrityGuard(run, db).authorize_runtime_changes()
                        if control_baseline.is_file()
                        else ()
                    )
                    for path in runtime_changed:
                        await runtime._publish_code_profile_event(
                            ctx,
                            "file_changed",
                            "completed",
                            path,
                            {"path": path},
                        )
                    await runtime._publish_code_profile_event(ctx, "verify", "started")
                    report = verifier.verify()
                    outcome = report.outcome
                    await runtime._publish_code_profile_event(
                        ctx,
                        "verify",
                        "completed" if report.passed else "failed",
                        report.reason,
                    )
                    if report.passed:
                        passed_event = normalize_claude_code_runtime_event(
                            {
                                "type": "verifier_passed",
                                "status": "completed",
                                "summary": "verifier passed",
                            },
                            run_id=run.id,
                        )
                        await runtime._publish_code_profile_event(
                            ctx,
                            passed_event["phase"],
                            passed_event["status"],
                            passed_event.get("reason", ""),
                            {
                                key: value
                                for key, value in passed_event.items()
                                if key
                                not in {
                                    "version",
                                    "profile",
                                    "phase",
                                    "status",
                                    "run_id",
                                    "manifest_version",
                                    "reason",
                                }
                            },
                        )
                        if not (
                            str(getattr(run, "snapshot_id", "") or "").strip()
                            and str(getattr(run, "snapshot_hash", "") or "").strip()
                            and str(getattr(run, "source_scan_report_id", "") or "").strip()
                        ):
                            outcome = "patch_ready"
                            run.status = "patch_ready"
                            run.failure_reason = "patch_ready"
                            db.commit()
                            await runtime._publish_code_profile_event(
                                ctx,
                                "seal",
                                "completed",
                                "sealed artifact not required for sandbox git workspace",
                            )
                            break
                        from app.services.code_agent.artifacts import CodeArtifactSealer

                        sealer = code_artifact_sealer or CodeArtifactSealer(
                            db, run, ctx.tool_executor.runner
                        )
                        await runtime._publish_code_profile_event(ctx, "seal", "started")
                        sealing = sealer.seal()
                        outcome = sealing.outcome
                        await runtime._publish_code_profile_event(
                            ctx,
                            "seal",
                            "completed" if sealing.sealed else "failed",
                            sealing.reason,
                        )
                        if sealing.sealed:
                            sealed_event = normalize_claude_code_runtime_event(
                                {
                                    "type": "artifact_sealed",
                                    "status": "completed",
                                    "artifact_id": sealing.artifact_id,
                                    "summary": "artifact sealed",
                                },
                                run_id=run.id,
                            )
                            await runtime._publish_code_profile_event(
                                ctx,
                                sealed_event["phase"],
                                sealed_event["status"],
                                sealed_event.get("reason", ""),
                                {
                                    key: value
                                    for key, value in sealed_event.items()
                                    if key
                                    not in {
                                        "version",
                                        "profile",
                                        "phase",
                                        "status",
                                        "run_id",
                                        "manifest_version",
                                        "reason",
                                    }
                                },
                            )
                        break
                    # Integrity/policy failures are terminal safety decisions,
                    # not coding feedback. Retrying could let the runtime make
                    # more changes after the Workspace boundary was violated.
                    if outcome in {"workspace_integrity_error", "policy_rejected"}:
                        break
                    if outcome == "budget_exhausted" or retry_attempt >= max_retries:
                        break
                    retry_attempt += 1
                    result = claude_code_verifier_feedback(report)
                    retry_event = normalize_claude_code_runtime_event(
                        {
                            "type": "verifier_failed_retrying",
                            "status": "retrying",
                            "retry_attempt": retry_attempt,
                            "max_retries": max_retries,
                            "summary": result,
                        },
                        run_id=run.id,
                    )
                    await runtime._publish_code_profile_event(
                        ctx,
                        retry_event["phase"],
                        retry_event["status"],
                        retry_event.get("reason", ""),
                        {
                            key: value
                            for key, value in retry_event.items()
                            if key
                            not in {
                                "version",
                                "profile",
                                "phase",
                                "status",
                                "run_id",
                                "manifest_version",
                                "reason",
                            }
                        },
                    )
        else:
            result = await asyncio.wait_for(runtime.run(ctx), timeout=execution.timeout_seconds)
        # 发布/部署请求也按普通 Claude Code 任务处理；平台不追加 local_publish 专用阶段。
        reason = code_termination_reason(execution.run_id)
        if reason:
            outcome = reason
        elif run and run.status in {
            "budget_exhausted", "no_progress", "policy_rejected",
            "workspace_integrity_error", "infrastructure_error",
        }:
            outcome = run.status
        if outcome == "execution_completed" and run:
            await runtime._publish_code_profile_event(ctx, "verify", "started")
            report = verifier.verify()
            outcome = report.outcome
            await runtime._publish_code_profile_event(
                ctx,
                "verify",
                "completed" if report.passed else "failed",
                report.reason,
            )
            if report.passed:
                has_seal_evidence = (
                    str(getattr(run, "snapshot_id", "") or "").strip()
                    and str(getattr(run, "snapshot_hash", "") or "").strip()
                    and str(getattr(run, "source_scan_report_id", "") or "").strip()
                )
                if not has_seal_evidence:
                    outcome = "patch_ready"
                    run.status = "patch_ready"
                    run.failure_reason = "patch_ready"
                    db.commit()
                    await runtime._publish_code_profile_event(
                        ctx,
                        "seal",
                        "completed",
                        "sealed artifact not required for sandbox git workspace",
                    )
                else:
                    from app.services.code_agent.artifacts import CodeArtifactSealer

                    sealer = code_artifact_sealer or CodeArtifactSealer(
                        db, run, ctx.tool_executor.runner
                    )
                    await runtime._publish_code_profile_event(ctx, "seal", "started")
                    sealing = sealer.seal()
                    outcome = sealing.outcome
                    await runtime._publish_code_profile_event(
                        ctx,
                        "seal",
                        "completed" if sealing.sealed else "failed",
                        sealing.reason,
                    )
    except asyncio.TimeoutError as exc:
        request_code_termination(ctx.chat_key, "timed_out")
        _running[ctx.chat_key] = False
        outcome = "timed_out"
        failure = exc
    except BaseException as exc:
        _running[ctx.chat_key] = False
        if isinstance(exc, asyncio.CancelledError):
            outcome = "cancelled"
        elif run and str(getattr(run, "status", "") or "") in {
            "budget_exhausted", "coding_failed", "coding_timeout",
            "infrastructure_error", "model_unavailable", "mcp_config_failed",
            "no_progress", "policy_rejected", "runtime_unavailable",
            "skill_load_failed", "target_not_found", "verification_failed",
            "verification_inconclusive", "workspace_integrity_error",
        }:
            outcome = str(getattr(run, "status") or "")
        else:
            outcome = "failed"
        failure = exc

    if run:
        from app.services.code_agent.budget import update_budget_usage

        update_budget_usage(
            db,
            run,
            elapsed_seconds=round(time.monotonic() - started_at, 3),
            timeout_seconds=execution.timeout_seconds,
        )

    cleanup_error = None
    try:
        terminal_failure_reason = ""
        if outcome != "execution_completed":
            terminal_failure_reason = (
                str(getattr(run, "failure_reason", "") or "")
                if run is not None
                else ""
            ) or outcome
        await cleanup_code_resources(
            ctx.chat_key,
            execution.run_id,
            db=db,
            terminal_status=outcome,
            failure_reason=terminal_failure_reason,
        )
    except CodeCleanupError as exc:
        if run:
            run.status = "infrastructure_error"
            run.failure_reason = "cleanup_failed"
            db.commit()
        await runtime._publish_code_profile_event(ctx, "cleanup", "failed", "cleanup_failed")
        cleanup_error = exc
    else:
        await runtime._publish_code_profile_event(ctx, "cleanup", "completed")
    if publish_code_done:
        # Claude Code runs bypass AgentRuntime.run(), so they do not emit the
        # shared modular `done` event. Without this terminal event the web UI
        # remains in its optimistic streaming state forever, especially when
        # the runtime fails before producing a patch.
        from app.services.agent_runtime.hub import hub
        from app.services.code_agent.results import (
            format_patch_verification_output,
            serialize_code_result,
        )
        from app.models import CodeArtifact

        done_content = result or (str(failure) if failure else outcome)
        # Every CodeAgent turn ends with the authoritative patch outcome in
        # the assistant message. This is informational only: accepting or
        # committing the patch remains a separate explicit follow-up turn.
        artifact = (
            db.get(CodeArtifact, run.artifact_id)
            if run and getattr(run, "artifact_id", "")
            else None
        )
        done_content = "\n\n".join((
            done_content,
            format_patch_verification_output(serialize_code_result(run, artifact)),
        )).strip()
        runtime._save_assistant_message(
            ctx,
            done_content,
            steps=_code_profile_steps_for_message(run),
        )
        try:
            await hub.publish(ctx.chat_key, {
                "type": "done",
                # The terminal CodeAgent result is already redacted and is the
                # chat's authoritative Markdown output. Send it in full so
                # the UI does not briefly show an incomplete result card while
                # history is being rehydrated.
                "content": done_content,
                "content_truncated": False,
                "workplace_changed": bool(run and run.status == "patch_ready"),
            })
        except Exception:
            logger.exception("code runtime done publish failed run=%s", execution.run_id)
    if cleanup_error:
        raise cleanup_error
    if failure:
        raise failure
    return result
