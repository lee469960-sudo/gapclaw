"""AgentRuntime — top-level facade composing all agent runtime components.

Orchestrates the ReAct loop by composing:
- AgentContext (immutable invocation snapshot)
- AgentLoopState (mutable per-turn state)
- DecisionEngine (reply analysis, stall detection, decide pipeline)
- ToolExecutor (MCP execution with validation/tracking)
- Verifier (goal-achievement verification)
- ContextManager (structured context layer management)
- SystemPromptBuilder (message construction)

Usage:
    ctx = AgentContext.from_params(db=db, agent=agent, ...)
    runtime = AgentRuntime()
    final = await runtime.run(ctx)

    # Or use the drop-in replacement for run_react_loop:
    from app.services.agent_runtime import run_agent
    final = await run_agent(db, agent, session_id, user_message, username)
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.agent_runtime.context import AgentContext

logger = logging.getLogger(__name__)


class AgentRuntime:
    """Top-level agent runtime orchestrating the full ReAct loop.

    Composes all modular components. The modular path (_run_modular)
    uses DecisionEngine + Verifier + ContextManager for a clean loop.
    Falls back to the existing react_engine for complex export scenarios.
    """

    # ---- Public interface ----

    async def run(self, ctx: AgentContext) -> str:
        """Execute the full ReAct loop for a given invocation context.

        Returns the final assistant reply string.
        """
        from app.services.agent_runtime.hub import _running

        key = ctx.chat_key
        if _running.get(key):
            _running[key] = False
        _running[key] = True

        # Persist user turn immediately so refresh mid-run still shows the question
        self._save_user_message(ctx)

        # Data/export tasks keep using the mature intent → contract → MCP
        # pipeline while the modular runtime owns chat and generic tool turns.
        contract_intent = str(
            getattr(getattr(ctx, "turn_intent", None), "intent", "") or ""
        )
        if contract_intent in ("data_query", "export_report"):
            logger.info(
                "agent_run contract_path intent=%s agent=%s session=%s",
                contract_intent,
                ctx.agent.id,
                ctx.session_id,
            )
            from app.services.react_engine import run_react_loop as _run_loop

            return await _run_loop(
                db=ctx.db,
                agent=ctx.agent,
                session_id=ctx.session_id,
                user_message=ctx.user_message,
                username=ctx.username,
                workplace_dir=ctx.workplace_dir,
                workplace_files=ctx.workplace_files,
                message_meta=ctx.message_meta,
                persist_user_message=False,
                precomputed_turn_intent=ctx.turn_intent,
            )

        # Fast path: conversational (no tools configured)
        if self._is_conversational(ctx):
            try:
                logger.info(
                    "agent_run conversational agent=%s session=%s msg_len=%d",
                    ctx.agent.id, ctx.session_id, len(ctx.user_message or ""),
                )
                result = await self._run_conversational(ctx)
                is_summary = bool(
                    getattr(getattr(ctx, "turn_intent", None), "wants_session_summary", False)
                )
                self._save_assistant_message(
                    ctx,
                    result,
                    steps=[{
                        "type": "info",
                        "action": "session_summary_reply" if is_summary else "conversational_reply",
                        "title": "会话总结" if is_summary else "对话回复",
                        "status": "done",
                    }],
                )
                logger.info(
                    "agent_run conversational done agent=%s session=%s result_len=%d",
                    ctx.agent.id, ctx.session_id, len(result or ""),
                )
                return result
            except Exception:
                logger.exception("Conversational path failed, falling back to react_engine")
        else:
            try:
                logger.info(
                    "agent_run modular agent=%s session=%s msg_len=%d mcp=%d skills=%d",
                    ctx.agent.id, ctx.session_id, len(ctx.user_message or ""),
                    len(ctx.mcp_ids), len(ctx.skill_ids),
                )
                result, steps, saved_paths, files_written = await self._run_modular(ctx)
                self._save_assistant_message(ctx, result, steps=steps, saved_paths=saved_paths)
                await self._publish_modular_done(
                    ctx, result, files_written=files_written, saved_paths=saved_paths,
                )
                logger.info(
                    "agent_run modular done agent=%s session=%s result_len=%d",
                    ctx.agent.id, ctx.session_id, len(result or ""),
                )
                return result
            except Exception:
                logger.exception("Modular loop failed, falling back to react_engine")

        # Fallback: user already persisted above — skip duplicate user row
        logger.warning(
            "agent_run fallback agent=%s session=%s — using legacy react_engine",
            ctx.agent.id, ctx.session_id,
        )
        from app.services.react_engine import run_react_loop as _run_loop

        return await _run_loop(
            db=ctx.db,
            agent=ctx.agent,
            session_id=ctx.session_id,
            user_message=ctx.user_message,
            username=ctx.username,
            workplace_dir=ctx.workplace_dir,
            workplace_files=ctx.workplace_files,
            message_meta=ctx.message_meta,
            persist_user_message=False,
            precomputed_turn_intent=ctx.turn_intent,
        )

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
    def _slim_steps_for_meta(steps: list[dict] | None) -> list[dict]:
        """Keep title/status-oriented fields for ChatMessage.meta (no huge payloads)."""
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
            preview = s.get("preview")
            if isinstance(preview, str) and preview.strip():
                cleaned_preview = AgentRuntime._sanitize_step_text(preview)
                if cleaned_preview:
                    item["preview"] = cleaned_preview[:300]
            if s.get("status") == "error":
                content = s.get("content")
                if isinstance(content, str) and content.strip():
                    cleaned_content = AgentRuntime._sanitize_step_text(content)
                    if cleaned_content:
                        item["content"] = cleaned_content[:400]
            out.append(item)
        return out

    @staticmethod
    def _save_assistant_message(
        ctx: AgentContext,
        reply: str,
        *,
        steps: list[dict] | None = None,
        saved_paths: list[str] | None = None,
    ) -> None:
        """Persist the assistant reply + execution steps to ChatMessage."""
        import json as _json
        from app.models import ChatMessage
        from app.security import now_str
        from app.services.react_engine import _ensure_visible_run_steps

        content = (reply or "").strip() or "（本轮未产生文字回复；详见执行过程）"
        visible = _ensure_visible_run_steps(
            AgentRuntime._slim_steps_for_meta(steps),
            export_like=bool(getattr(ctx, "has_export_skill", False)),
            has_mcp=bool(ctx.mcp_ids),
        )
        meta = {
            "steps": visible,
            "step_count": max(len(visible), 1),
            "saved_paths": list(saved_paths or [])[:20],
        }
        if bool(
            getattr(getattr(ctx, "turn_intent", None), "wants_session_summary", False)
        ):
            meta["session_summary_reply"] = True
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

    @staticmethod
    def _is_conversational(ctx: AgentContext) -> bool:
        """True when the task is a tool-free chat (no MCP, no export skills, no RAG)."""
        turn_intent = getattr(ctx, "turn_intent", None)
        if turn_intent is not None:
            return bool(getattr(turn_intent, "is_chat", False))
        return (
            not ctx.mcp_ids
            and not ctx.httpmcp_ids
            and not ctx.rag_ids
            and not ctx.has_export_skill
            and not ctx.skill_ids
        )

    @staticmethod
    def _llm_step_preview(reply: str) -> str:
        """Short preview so consecutive LLM steps are not collapsed by the UI."""
        text = AgentRuntime._sanitize_step_text(reply)
        if not text:
            return "(empty)"
        text = " ".join(text.split())
        return text[:200] if text else "(tool/empty)"

    @staticmethod
    def _sanitize_step_text(text: str) -> str:
        """Strip protocol/meta reasoning so execution-step text stays user-safe."""
        from app.services.agent_runtime.decision_engine import DecisionEngine
        from app.services.tool_parser import extract_final_payload

        raw = str(text or "").strip()
        if not raw:
            return ""
        if DecisionEngine.is_final_reply(raw):
            cleaned = DecisionEngine.clean_final_answer(raw)
        else:
            cleaned = DecisionEngine.clean_display_text(raw)
            cleaned = re.sub(
                r"(?im)^\s*(?:actually|wait|looking at|let me|so i need|"
                r"the previous turn|i think|i notice)\b.*$",
                "",
                cleaned,
            )
            if re.search(r"(?im)^\s*FINAL\s*[:：]\s*", cleaned):
                cleaned = DecisionEngine.clean_final_answer(extract_final_payload(cleaned))
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
        from app.services.agent_runtime.hub import hub, _running
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
        finally:
            _running[key] = False

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
        if status == "error" and content:
            step["content"] = content[:400]
        await AgentRuntime._append_step(ctx, state, step)

    async def _run_conversational(self, ctx: AgentContext) -> str:
        """Single-turn conversational path — no tools, no ReAct loop.

        Uses ConversationalHandler for message construction and light fallback.
        """
        from app.services.agent_runtime.conversational import ConversationalHandler
        from app.services.agent_runtime.hub import _running, hub
        from app.services.agent_runtime.system_prompt import SystemPromptBuilder
        from app.services.llm_client import chat_completion
        from app.services.agent_runtime.decision_engine import DecisionEngine
        from app.models import ChatMessage
        from app.services.react_engine import (
            _build_session_summary_fallback,
            _build_session_summary_messages,
        )

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
        is_summary = bool(
            getattr(getattr(ctx, "turn_intent", None), "wants_session_summary", False)
        )
        if is_summary:
            messages = _build_session_summary_messages(
                agent=ctx.agent,
                history=history,
                user_message=ctx.user_message,
                note_content=ctx.note_content,
            )
        else:
            messages = ConversationalHandler.build_messages(
                ctx.agent, history, effective,
            )
        if not is_summary and (ctx.note_content or "").strip():
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
                    max_tokens=4096 if is_summary else 1024,
                    db=ctx.db,
                    timeout=getattr(ctx.agent, 'llm_timeout', None) or 60,
                )
                final = DecisionEngine.clean_final_answer(reply or "")
                if not final.strip():
                    raise RuntimeError("empty conversational reply")
            except Exception:
                logger.exception("Conversational LLM failed, using light fallback")
                if is_summary:
                    final = _build_session_summary_fallback(
                        history=history,
                        user_message=ctx.user_message,
                        note_content=ctx.note_content,
                    )
                else:
                    final = SystemPromptBuilder.build_light_agent_reply(
                        ctx.agent, ctx.user_message,
                    )

            # Publish to WebSocket hub
            try:
                await hub.publish(key, {
                    "type": "step",
                    "op": "append",
                    "index": 0,
                    "step": {
                        "type": "info",
                        "action": "session_summary_reply" if is_summary else "conversational_reply",
                        "title": "会话总结" if is_summary else "对话回复",
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

    # ---- Modular ReAct loop ----

    async def _run_modular(self, ctx: AgentContext) -> tuple[str, list[dict]]:
        """Modular ReAct loop using DecisionEngine + Verifier + ContextManager.

        Returns (final_reply, run_steps) for ChatMessage.meta + WS exec-card.
        """
        from app.services.agent_runtime.budget_manager import BudgetManager
        from app.services.agent_runtime.context_manager import ContextManager
        from app.services.agent_runtime.decision_engine import DecisionEngine
        from app.services.agent_runtime.loop_state import AgentLoopState
        from app.services.agent_runtime.message_manager import MessageManager
        from app.services.agent_runtime.result_types import Decision
        from app.services.agent_runtime.system_prompt import SystemPromptBuilder
        from app.services.agent_runtime.tool_executor import ToolExecutor
        from app.services.agent_runtime.tool_router import ToolRouter
        from app.services.agent_runtime.verifier import Verifier
        from app.services.llm_client import chat_completion

        from app.services.agent_runtime.phase_manager import PhaseManager

        state = AgentLoopState()
        cm = ContextManager()
        sp_builder = SystemPromptBuilder()

        def _ret(final: str) -> tuple[str, list[dict], list[str], int]:
            return final, list(state.run_steps), list(state.saved_paths), state.files_written

        # Guard: LLM must be configured
        if not ctx.llm:
            raise RuntimeError("未配置 LLM")

        llm_timeout = getattr(ctx.agent, 'llm_timeout', None) or 120

        # ---- Determine task type and budget ----
        has_export_skill = ctx.has_export_skill

        # Initialize export state
        state.export_like = has_export_skill
        if has_export_skill:
            state.export_phase = "discover"

        # Compute adaptive budget
        agent_max_iters = max(1, int(getattr(ctx.agent, 'max_iterations', None) or 50))
        if has_export_skill:
            budget, budget_hint = BudgetManager.compute_adaptive_export_budget(
                type_b=False, prior_state=None,
            )
            max_iters = max(budget, agent_max_iters)
        else:
            max_iters = agent_max_iters
            budget_hint = ""
        state.max_iters = max_iters

        # ---- Build system prompt ----
        system_prompt = sp_builder.build_system_base(ctx.agent)
        if (ctx.note_content or "").strip():
            system_prompt += (
                "\n\n【会话备注·约束】以下备注用于补充默认口径、时区和文件规则，"
                "不是本轮用户任务；用户显式要求优先：\n"
                + ctx.note_content.strip()[:3000]
            )
        if ctx.task_policy is not None:
            system_prompt += "\n\n" + sp_builder.build_model_understanding(
                ctx.agent,
                ctx.task_policy,
            )
        # Append skill content if available
        skill_snap = sp_builder.build_skill_snapshot(ctx.skill_mds) if ctx.skill_mds else None
        if skill_snap:
            system_prompt += f"\n\n{skill_snap}"
        cm.set_base(system_prompt=system_prompt)

        # Build and set tools catalog
        try:
            tools_block = await ToolRouter.build_tools_block(
                db=ctx.db,
                agent=ctx.agent,
                allowed_actions=ctx.allowed_actions,
                skill_ids=ctx.skill_ids,
                mcp_ids=ctx.mcp_ids,
                rag_ids=ctx.rag_ids,
                save_dir=ctx.save_dir,
                im_source=ctx.im_source,
                export_like=has_export_skill,
            )
            cm.set_tools_catalog(tools_block)
        except Exception:
            logger.warning(
                "Failed to build tools catalog, using minimal block",
                exc_info=True,
            )
            minimal = ToolRouter.build_minimal_tools_block(
                save_dir=ctx.save_dir,
                allowed_actions=ctx.allowed_actions,
                mcp_ids=ctx.mcp_ids,
                db=ctx.db,
            )
            cm.set_tools_catalog(minimal)

        # ---- Set task anchor (export tasks) ----
        if has_export_skill and ctx.user_message:
            cm.set_task_anchor(f"用户需求: {ctx.user_message[:2000]}")

        # Append user message
        cm.push_user_message(ctx.user_message)

        # ---- Inject budget hint ----
        if budget_hint:
            cm.push_coach_hint(budget_hint)

        # ---- Resolve sandbox ----
        sandbox = ctx.sandbox
        if not sandbox and getattr(ctx.agent, 'sandbox_id', None):
            try:
                from app.services import docker_service
                sandbox = docker_service.get_sandbox(ctx.db, ctx.agent.sandbox_id)
            except Exception:
                pass

        # ---- Inject workplace directory listing ----
        try:
            if ctx.agent.sandbox_id:
                from app.services.workplace import format_dir_listing
                listing = format_dir_listing(ctx.agent.sandbox_id, ctx.save_dir)
                if listing.strip():
                    dir_hint = SystemPromptBuilder.build_workplace_listing_hint(listing)
                    cm.push_coach_hint(dir_hint)
        except Exception:
            pass

        # ---- Visible load steps (align monolith skill_loaded / mcp_loaded) ----
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

        # Guardrail: warn if bindings exist but required action types are missing
        if ctx.mcp_ids and "mcp_tool_call" not in ctx.allowed_actions:
            logger.warning(
                "modular_loop agent=%s has %d MCPs bound but mcp_tool_call not in allowed_actions",
                ctx.agent.id, len(ctx.mcp_ids),
            )
            cm.push_coach_hint(
                "【配置警告】Agent 已绑定 MCP 数据源，但未开启 mcp_tool_call 权限。"
                "如需使用 MCP 工具，请在 Agent 设置中将 mcp_tool_call 添加到允许的操作列表中。"
            )
        if ctx.skill_ids and not any(
            a in ctx.allowed_actions for a in ("skill_read_md", "skill_run_script")
        ):
            logger.warning(
                "modular_loop agent=%s has %d Skills bound but skill actions not in allowed_actions",
                ctx.agent.id, len(ctx.skill_ids),
            )
            cm.push_coach_hint(
                "【配置警告】Agent 已绑定 Skill，但未开启 skill_read_md/skill_run_script 权限。"
                "如需使用 Skill，请在 Agent 设置中添加相应权限。"
            )

        # ---- Main ReAct loop ----
        logger.info(
            "modular_loop start agent=%s session=%s max_iters=%d export=%s",
            ctx.agent.id, ctx.session_id, max_iters, has_export_skill,
        )
        tool_call_count = 0
        for iteration in range(max_iters):
            # Check cancellation
            from app.services.agent_runtime.hub import _running
            if not _running.get(ctx.chat_key, False):
                logger.info("modular_loop cancelled agent=%s iter=%d", ctx.agent.id, iteration)
                return _ret(state.final or "任务已取消")

            round_no = iteration + 1
            await self._append_step(ctx, state, {
                "type": "llm",
                "iteration": round_no,
                "title": f"LLM 推理 (第 {round_no} 轮)",
                "status": "running",
            })

            # 1. Call LLM
            try:
                reply = await chat_completion(
                    ctx.llm,
                    list(cm.messages),
                    max_tokens=4096,
                    db=ctx.db,
                    timeout=llm_timeout,
                    cancel_check=lambda: not _running.get(ctx.chat_key, False),
                )
            except Exception as exc:
                from app.services.agent_runtime.hub import ChatStopped
                if isinstance(exc, ChatStopped) or not _running.get(ctx.chat_key, False):
                    await self._patch_last_step(
                        ctx, state, status="error", content="已停止",
                    )
                    return _ret(state.final or "[已停止]")
                state.llm_failure_streak += 1
                error_detail = f"{type(exc).__name__}: {exc!r}"
                logger.error(
                    "LLM call failed iter=%s streak=%s type=%s error=%r",
                    iteration,
                    state.llm_failure_streak,
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
                await self._patch_last_step(
                    ctx, state, status="error", content=error_detail[:400],
                )
                state.empty_llm_streak += 1
                if state.llm_failure_streak >= 2:
                    return _ret(
                        state.final
                        or "LLM 服务连续调用失败，任务已暂停；本轮执行记录已保留，请稍后继续。"
                    )
                continue

            reply = reply or ""
            state.llm_failure_streak = 0
            state.last_reply = reply
            await self._patch_last_step(
                ctx, state,
                status="done",
                preview=self._llm_step_preview(reply),
            )

            # 2. Classify reply + track empty streaks
            reply_class = DecisionEngine.classify_reply(reply)
            if reply_class == "empty":
                state.empty_llm_streak += 1
            else:
                state.empty_llm_streak = 0

            # Append assistant reply to history
            cm.push_assistant_reply(reply)

            # ---- Export phase transitions ----
            if state.export_like:
                remaining = max_iters - iteration - 1
                # Force finalize when budget nearly exhausted
                if remaining <= 3 and state.export_phase != "finalize":
                    PhaseManager.enter_export_finalize(state, reason="budget low")
                    cm.push_coach_hint(
                        PhaseManager.format_finalize_hint(
                            state,
                            save_dir=ctx.save_dir,
                            shell_enabled="shell" in (ctx.allowed_actions or []),
                        )
                    )
                # Discover → Plan: after first tool execution
                elif state.export_phase == "discover" and state.ran_any_tool:
                    PhaseManager.enter_export_plan(state, reason="tool executed")
                # Plan → Fetch: when LLM starts executing tools after PLAN
                elif state.export_phase == "plan" and reply_class == "tool":
                    PhaseManager.enter_export_fetch(state, budget=remaining)
                # Fetch → Analyze: when no more tool calls, LLM starts reasoning
                elif state.export_phase == "fetch" and reply_class in ("text", "final") and state.ran_any_tool:
                    PhaseManager.enter_export_analyze(state, reason="no more tools")

            # 3. Decide next action
            decision = DecisionEngine.decide(
                reply, state=state, ran_any_tool=state.ran_any_tool,
                export_like=state.export_like,
                export_phase=state.export_phase,
            )

            # 4. Handle FINISH
            if decision == Decision.FINISH:
                state.final = DecisionEngine.clean_final_answer(reply)
                from app.services.intent_router import (
                    UNAVAILABLE_DATA_TOOLS_COACH,
                    append_unavailable_tools_soft_nudge,
                    maybe_soft_reject_unavailable_tools_finish,
                )
                excuse_action, state.mcp_excuse_soft_rejects, excuse_hit = (
                    await maybe_soft_reject_unavailable_tools_finish(
                        llm=ctx.llm,
                        assistant_text=state.final or reply,
                        has_mcp=bool(ctx.mcp_ids),
                        ran_any_tool=state.ran_any_tool,
                        soft_reject_count=state.mcp_excuse_soft_rejects,
                        db=ctx.db,
                        timeout=min(30, int(llm_timeout or 30)),
                    )
                )
                if excuse_hit:
                    state.mcp_excuse_claim_hit = True
                if excuse_action == "reject":
                    logger.info(
                        "modular_loop soft_reject_mcp_excuse agent=%s iter=%d count=%d",
                        ctx.agent.id, iteration, state.mcp_excuse_soft_rejects,
                    )
                    cm.push_coach_hint(UNAVAILABLE_DATA_TOOLS_COACH)
                    state.no_progress += 1
                    continue
                if excuse_action == "append":
                    state.final = append_unavailable_tools_soft_nudge(state.final or "")
                verif = Verifier.verify_deliverable(state.final)
                if verif.success or state.no_progress >= 5:
                    logger.info(
                        "modular_loop finish agent=%s iter=%d/%d tools=%d verified=%s",
                        ctx.agent.id, iteration, max_iters, tool_call_count, verif.success,
                    )
                    return _ret(state.final)
                logger.info(
                    "modular_loop finish_rejected agent=%s iter=%d reason=%s",
                    ctx.agent.id, iteration, verif.reason,
                )
                # Deliverable verification failed — nudge and retry
                cm.push_coach_hint(
                    f"FINAL 验证未通过: {verif.reason}。{verif.suggestion or '请补充明确交付物后重新 FINAL。'}"
                )
                state.no_progress += 1
                continue

            # 5. Handle tool execution
            if reply_class == "tool":
                tool_call_count += 1
                state.text_only_streak = 0
                tool_steps = DecisionEngine.extract_tool_steps(reply)
                if not tool_steps:
                    # Fallback: parse with detect_action
                    action_type_str, normalized_line = DecisionEngine.detect_action(
                        reply,
                        ctx.allowed_actions,
                    )
                    tool_steps = (
                        [{"action": action_type_str, "normalized": normalized_line}]
                        if action_type_str and normalized_line
                        else []
                    )

                if not tool_steps:
                    state.no_progress += 1
                    cm.push_coach_hint(
                        "未解析到已启用的工具调用。请只使用当前工具目录中的协议；"
                        "不要尝试未启用的 SHELL。"
                    )
                    continue

                for step in tool_steps:
                    if isinstance(step, dict):
                        action = step.get("action", "")
                        normalized = step.get("normalized", "")
                    else:
                        action = step.action
                        normalized = step.reply
                    if not action or not normalized:
                        continue

                    if action not in ctx.allowed_actions:
                        state.no_progress += 1
                        cm.push_coach_hint(
                            f"工具 `{action}` 未启用，已阻止执行。"
                            "请改用当前工具目录中的能力。"
                        )
                        await self._append_tool_step(
                            ctx,
                            state,
                            iteration=round_no,
                            action="permission_denied",
                            title=f"已阻止未启用工具: {action}",
                            status="error",
                            content="Agent 权限配置未启用该工具，未执行。",
                        )
                        continue

                    # Execute
                    try:
                        tool_result = await ToolExecutor.execute(
                            action, normalized,
                            ctx.db, ctx.agent, sandbox,
                            ctx.skill_ids, ctx.mcp_ids,
                            ctx.httpmcp_ids, ctx.rag_ids,
                        )
                    except Exception as exc:
                        tool_result = f"工具执行异常: {exc}"

                    state.ran_any_tool = True
                    result_text = tool_result or ""

                    # Extract saved_paths from file_write results
                    if action in ("file_write",) and result_text:
                        import re as _re
                        _m = _re.search(r"已写入\s+(\S+)", result_text)
                        if _m:
                            _path = _m.group(1)
                            if _path not in state.saved_paths:
                                state.saved_paths.append(_path)
                            state.files_written += 1
                            MessageManager.append_progress(
                                state.progress_lines, f"已写入 {_path}",
                            )

                    # Record MCP metadata
                    if action in ("mcp_tool_call", "httpmcp_call"):
                        ToolExecutor.record_mcp_result(
                            state.mcp_results, normalized, result_text,
                        )

                    # Parse tool_args for MCP verification
                    _parsed_args: dict = {}
                    if action in ("mcp_tool_call", "httpmcp_call"):
                        import re as _re2, json as _json
                        _m2 = _re2.search(r"(?<![A-Za-z/])MCP:\s*(\S+)\s*(.*)", normalized)
                        if _m2 and _m2.group(2).strip():
                            try:
                                _parsed_args = _json.loads(_m2.group(2))
                            except Exception:
                                _parsed_args = {"raw": _m2.group(2).strip()[:200]}

                    # Verify tool outcome
                    verification = Verifier.verify_tool_result(
                        action_type=action,
                        tool_name=action,
                        tool_args=_parsed_args,
                        tool_output=result_text,
                    )
                    cm.push_observation(verification)
                    cm.push_tool_result(result_text, action=action)

                    # Track failures
                    if not verification.success:
                        state.no_progress += 1
                        if ToolExecutor.is_tool_failure(result_text):
                            hint_text = ToolExecutor.track_mcp_failure(
                                tool=action, normalized=normalized,
                                error_text=result_text,
                                mcp_tool_calls=state.mcp_tool_calls,
                                mcp_tool_fails=state.mcp_tool_fails,
                                mcp_identical_counts=state.mcp_identical_counts,
                                mcp_class_fails=state.mcp_class_fails,
                                mcp_class_samples=state.mcp_class_samples,
                                mcp_class_tools=state.mcp_class_tools,
                                soft_fail_limit=state.soft_fail_limit,
                                class_fail_limit=state.class_fail_limit,
                            )
                            if hint_text:
                                cm.push_coach_hint(hint_text)

                        if verification.next_action == "replan":
                            from app.services.agent_runtime.replanner import Replanner
                            retry_hint = Replanner.generate_retry_hint(
                                verification, tool_name=action,
                            )
                            cm.push_coach_hint(retry_hint)
                    else:
                        state.no_progress = max(0, state.no_progress - 1)
                        MessageManager.append_progress(
                            state.progress_lines,
                            f"[{action}] {verification.reason}",
                        )
                        if verification.suggestion:
                            cm.push_coach_hint(verification.suggestion)

                    await self._append_tool_step(
                        ctx, state,
                        iteration=round_no,
                        action=action,
                        title=f"[{action}] {verification.reason[:80] if verification else ''}",
                        status="done" if verification and verification.success else "error",
                        content=result_text[:300] if result_text else "",
                    )

                # Update progress block
                if state.progress_lines:
                    cm.set_progress_block(state.progress_lines)

            # 5b. Handle CONTINUE for text/plan replies — inject hint on repeated text-only rounds
            if decision == Decision.CONTINUE and reply_class in ("text", "plan"):
                state.text_only_streak += 1
                state.no_progress += 1
                if state.text_only_streak == 3:
                    tool_examples = "MCP:、READ: 或 WRITE:"
                    if "shell" in ctx.allowed_actions:
                        tool_examples += "、SHELL:"
                    cm.push_coach_hint(
                        "【提示】你已连续多轮只输出文字分析，没有执行任何实际操作。"
                        f"请立即选择一个具体工具来推进任务（如 {tool_examples}），"
                        "或者如果任务已完成，请输出 FINAL: 总结。"
                    )
                elif state.text_only_streak >= 6:
                    cm.push_coach_hint(
                        "【严重警告】你已连续 6 轮没有执行任何工具。"
                        "如果无法继续，请立即输出 FINAL: 说明当前进度。"
                    )
            elif decision == Decision.CONTINUE and reply_class not in ("text", "plan"):
                state.text_only_streak = 0

            # 6. Handle REPLAN / stalling
            stalling = DecisionEngine.is_stalling(state)
            if (
                stalling
                and ctx.mcp_ids
                and not state.mcp_results
            ):
                raise RuntimeError(
                    "MCP tool protocol stalled: bound MCP produced no executable MCP action"
                )
            if decision == Decision.REPLAN or stalling:
                logger.info(
                    "modular_loop replan agent=%s iter=%d/%d no_progress=%d decision=%s stalling=%s",
                    ctx.agent.id, iteration, max_iters, state.no_progress,
                    decision, stalling,
                )
                from app.services.agent_runtime.replanner import Replanner, ReplanContext
                rctx = ReplanContext(
                    consecutive_failures=state.no_progress,
                    budget_remaining=max_iters - iteration - 1,
                )
                hint = Replanner.generate_budget_warning_hint(
                    max_iters - iteration - 1, total_budget=max_iters,
                )
                replan_hint = Replanner.generate_replan_hint(rctx)
                cm.push_coach_hint(f"{hint}\n\n{replan_hint}")

            # 7. Trim context periodically
            if iteration > 0 and iteration % 8 == 0:
                cm.trim_tool_results()

        # Budget exhausted
        logger.warning(
            "modular_loop budget_exhausted agent=%s iters=%d/%d tools=%d final=%s",
            ctx.agent.id, max_iters, max_iters, tool_call_count, bool(state.final),
        )
        if state.final:
            return _ret(DecisionEngine.clean_final_answer(state.final))
        if state.last_reply:
            force_msg = ""
            if state.export_like and state.ran_any_tool:
                force_msg = "\n\n【引擎提示】预算已耗尽，以上为当前可用数据。"
            return _ret(DecisionEngine.clean_final_answer(state.last_reply + force_msg))
        return _ret("任务已达最大迭代次数，请检查结果。")


# ---- Drop-in replacement for run_react_loop ----


async def run_agent(
    db,
    agent,
    session_id: str,
    user_message: str,
    username: str,
    workplace_dir: str = "",
    workplace_files: list[str] | None = None,
    message_meta: dict | None = None,
) -> str:
    """Drop-in replacement for run_react_loop using the modular AgentRuntime.

    Resolves agent configuration (LLM, sandbox, MCP bindings) from the DB,
    builds an AgentContext, and delegates to AgentRuntime.run().

    Falls back to the legacy react_engine path automatically on failure.
    """
    from app.models import LLMResource, Sandbox, ChatMessage, ChatNote
    from app.services.agent_runtime.context import AgentContext
    from app.services.agent_runtime.utils import _bound_mcp_names
    from app.services.task_policy import load_skill_mds

    # Resolve entities (mirrors run_react_loop startup)
    llm = db.query(LLMResource).filter(LLMResource.id == agent.llm_id).first()
    sandbox = db.query(Sandbox).filter(Sandbox.id == agent.sandbox_id).first()

    allowed = json.loads(agent.allowed_actions or "[]")
    skill_ids = json.loads(agent.skills or "[]")
    mcp_ids = json.loads(agent.mcps or "[]")

    # Auto-include required action types when corresponding bindings exist
    if mcp_ids and "mcp_tool_call" not in allowed:
        allowed.append("mcp_tool_call")
        logger.info("run_agent auto-added mcp_tool_call for agent=%s", agent.id)
    if skill_ids:
        if "skill_read_md" not in allowed:
            allowed.append("skill_read_md")
        if "skill_run_script" not in allowed:
            allowed.append("skill_run_script")
    rag_ids = json.loads(getattr(agent, "rags", None) or "[]")
    httpmcp_ids: list[str] = []

    save_dir = workplace_dir.strip().strip("/")

    user_meta = dict(message_meta or {})

    # Keep the note as an independent system constraint, never as user-task text.
    note = db.query(ChatNote).filter(
        ChatNote.agent_id == agent.id, ChatNote.session_id == session_id
    ).first()
    effective_message = user_message
    note_content = ""
    if note and note.content and note.content.strip():
        note_content = note.content.strip()

    skill_mds = load_skill_mds(db, skill_ids)
    skill_names = [n for n, _md in (skill_mds or []) if n]
    mcp_names = _bound_mcp_names(db, mcp_ids)
    # Detect export skill by name (ids are opaque)
    has_export_skill = any("export" in (n or "").lower() for n in skill_names)
    skill_blob = "\n\n".join(md for _name, md in (skill_mds or []) if md)

    from app.services.intent_router import (
        analyze_turn_intent,
        build_task_relation_context,
        looks_like_task_message,
        task_policy_from_intent,
    )
    from app.services.skill_lesson import find_repair_base_run_state

    intent_text = (user_message or "").strip()
    is_light_chat = bool(
        intent_text
        and len(intent_text) <= 40
        and not looks_like_task_message(intent_text)
    )

    relation_history = list(reversed(
        db.query(ChatMessage).filter(
            ChatMessage.agent_id == agent.id,
            ChatMessage.session_id == session_id,
        ).order_by(ChatMessage.id.desc()).limit(
            max(1, min(int(agent.history_length or 30), 200))
        ).all()
    ))
    prior_export = (
        find_repair_base_run_state(
            agent.sandbox_id,
            session_id=session_id or "",
        )
        if agent.sandbox_id
        else None
    )
    turn_intent = await analyze_turn_intent(
        llm,
        user_message,
        has_export_skill=has_export_skill,
        skill_blob=skill_blob,
        db=db,
        is_light_chat=is_light_chat,
        timeout=min(45, int(getattr(agent, "llm_timeout", None) or 45)),
        agent_id=agent.id,
        session_id=session_id,
        max_attempts=2,
        context_note=note_content,
        conversation_context=build_task_relation_context(
            relation_history,
            prior_export[1] if prior_export else None,
        ),
        require_decided_relation=bool(prior_export),
    )
    task_policy = task_policy_from_intent(turn_intent)

    # Build context
    ctx = AgentContext.from_params(
        db=db,
        agent=agent,
        session_id=session_id,
        user_message=user_message,
        username=username,
        workplace_dir=workplace_dir,
        workplace_files=workplace_files or [],
        message_meta=message_meta,
        llm=llm,
        sandbox=sandbox,
        allowed_actions=allowed,
        skill_ids=skill_ids,
        mcp_ids=mcp_ids,
        rag_ids=rag_ids,
        httpmcp_ids=httpmcp_ids,
        skill_mds=skill_mds,
        skill_blob=skill_blob,
        skill_names=skill_names,
        mcp_names=mcp_names,
        has_export_skill=has_export_skill,
        save_dir=save_dir,
        effective_message=effective_message,
        note_content=note_content,
        user_meta=user_meta,
        turn_intent=turn_intent,
        task_policy=task_policy,
    )

    runtime = AgentRuntime()
    return await runtime.run(ctx)
