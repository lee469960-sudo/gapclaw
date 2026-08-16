"""AgentRuntime — top-level facade for the single LLM-driven ReAct loop.

Composes:
- AgentContext (immutable invocation snapshot)
- AgentLoopState (mutable per-turn state)
- DecisionEngine (reply parsing/cleaning only — no gate decisions)
- ToolExecutor (execution + security handled by the loop)
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
            result, steps, saved_paths, files_written = await self._run_modular(ctx)
            self._save_assistant_message(ctx, result, steps=steps, saved_paths=saved_paths)
            await self._publish_modular_done(
                ctx, result, files_written=files_written, saved_paths=saved_paths,
            )
            return result
        finally:
            _running[key] = False

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
    ) -> None:
        """Persist the assistant reply + execution steps to ChatMessage."""
        import json as _json
        from app.models import ChatMessage
        from app.security import now_str

        content = (reply or "").strip() or "（本轮未产生文字回复；详见执行过程）"
        visible = AgentRuntime._ensure_visible_run_steps(
            AgentRuntime._slim_steps_for_meta(steps),
        )
        meta = {
            "steps": visible,
            "step_count": max(len(visible), 1),
            "saved_paths": list(saved_paths or [])[:20],
        }
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
        """True when the task is a tool-free chat (no MCP, RAG, or skills bound)."""
        return (
            not ctx.mcp_ids
            and not ctx.httpmcp_ids
            and not ctx.rag_ids
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
        if status == "error" and content:
            step["content"] = content[:400]
        await AgentRuntime._append_step(ctx, state, step)

    async def _run_conversational(self, ctx: AgentContext) -> str:
        """Single-turn conversational path — no tools, no ReAct loop."""
        from app.services.agent_runtime.conversational import ConversationalHandler
        from app.services.agent_runtime.hub import _running, hub
        from app.services.agent_runtime.system_prompt import SystemPromptBuilder
        from app.services.llm_client import chat_completion
        from app.services.agent_runtime.decision_engine import DecisionEngine
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
                final = DecisionEngine.clean_final_answer(reply or "")
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

    # ---- LLM-driven ReAct loop ----

    async def _run_modular(self, ctx: AgentContext) -> tuple[str, list[dict], list[str], int]:
        """Single LLM-driven loop: parse → security-gate → execute → observe.

        The LLM decides when to continue, switch tools, or finish. The engine
        only parses protocol lines, blocks disallowed actions, and feeds results
        back into context. Returns (final_reply, run_steps, saved_paths, files_written).
        """
        from app.services.agent_runtime.context_manager import ContextManager
        from app.services.agent_runtime.decision_engine import DecisionEngine
        from app.services.agent_runtime.hub import ChatStopped, _running
        from app.services.agent_runtime.loop_state import AgentLoopState
        from app.services.agent_runtime.message_manager import MessageManager
        from app.services.agent_runtime.system_prompt import SystemPromptBuilder
        from app.services.agent_runtime.tool_executor import ToolExecutor
        from app.services.agent_runtime.tool_router import ToolRouter
        from app.services.llm_client import chat_completion

        state = AgentLoopState()
        cm = ContextManager()
        sp_builder = SystemPromptBuilder()

        def _ret(final: str) -> tuple[str, list[dict], list[str], int]:
            return final, list(state.run_steps), list(state.saved_paths), state.files_written

        if not ctx.llm:
            raise RuntimeError("未配置 LLM")

        llm_timeout = getattr(ctx.agent, 'llm_timeout', None) or 120
        max_iters = max(1, int(getattr(ctx.agent, 'max_iterations', None) or 50))
        state.max_iters = max_iters

        # ---- System prompt ----
        system_prompt = sp_builder.build_system_base(ctx.agent)
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

        # ---- Tools catalog ----
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
            )
            cm.set_tools_catalog(tools_block)
        except Exception:
            logger.warning("Failed to build tools catalog, using minimal block", exc_info=True)
            cm.set_tools_catalog(ToolRouter.build_minimal_tools_block(
                save_dir=ctx.save_dir,
                allowed_actions=ctx.allowed_actions,
                mcp_ids=ctx.mcp_ids,
                db=ctx.db,
            ))

        cm.push_user_message(ctx.user_message)

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
                    cm.push_coach_hint(SystemPromptBuilder.build_workplace_listing_hint(listing))
        except Exception:
            pass

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

        # ---- Config guardrail warnings (soft coach hints, not hard blocks) ----
        if ctx.mcp_ids and "mcp_tool_call" not in ctx.allowed_actions:
            cm.push_coach_hint(
                "【配置警告】Agent 已绑定 MCP 数据源，但未开启 mcp_tool_call 权限。"
                "如需使用 MCP 工具，请在 Agent 设置中将 mcp_tool_call 添加到允许的操作列表中。"
            )
        if ctx.skill_ids and not any(
            a in ctx.allowed_actions for a in ("skill_read_md", "skill_run_script")
        ):
            cm.push_coach_hint(
                "【配置警告】Agent 已绑定 Skill，但未开启 skill_read_md/skill_run_script 权限。"
                "如需使用 Skill，请在 Agent 设置中添加相应权限。"
            )

        # ---- Main loop ----
        logger.info(
            "modular_loop start agent=%s session=%s max_iters=%d",
            ctx.agent.id, ctx.session_id, max_iters,
        )
        tool_call_count = 0
        text_only_streak = 0
        llm_failures = 0

        for iteration in range(max_iters):
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
                if isinstance(exc, ChatStopped) or not _running.get(ctx.chat_key, False):
                    await self._patch_last_step(ctx, state, status="error", content="已停止")
                    return _ret(state.final or "[已停止]")
                llm_failures += 1
                error_detail = f"{type(exc).__name__}: {exc!r}"
                logger.error(
                    "LLM call failed iter=%s type=%s error=%r",
                    iteration, type(exc).__name__, exc, exc_info=True,
                )
                await self._patch_last_step(
                    ctx, state, status="error", content=error_detail[:400],
                )
                if llm_failures >= 2:
                    return _ret(
                        state.final
                        or "LLM 服务连续调用失败，任务已暂停；本轮执行记录已保留，请稍后继续。"
                    )
                continue

            reply = reply or ""
            llm_failures = 0
            state.last_reply = reply
            await self._patch_last_step(
                ctx, state, status="done", preview=self._llm_step_preview(reply),
            )
            cm.push_assistant_reply(reply)

            # 2. Parse tool steps
            tool_steps = DecisionEngine.extract_tool_steps(reply)

            # 2a. FINAL → task complete
            final_step = next((s for s in tool_steps if getattr(s, "is_final", False)), None)
            if final_step is not None:
                state.final = DecisionEngine.clean_final_answer(final_step.reply or reply)
                logger.info(
                    "modular_loop finish agent=%s iter=%d/%d tools=%d",
                    ctx.agent.id, iteration, max_iters, tool_call_count,
                )
                return _ret(state.final)

            # 2b. Text/plan reply with no tool — soft nudge after a few rounds
            if not tool_steps:
                text_only_streak += 1
                if text_only_streak == 3:
                    cm.push_coach_hint(
                        "【提示】你已连续多轮只输出文字分析，没有执行实际操作。"
                        "请选择一个工具推进任务，或若已完成请输出 FINAL: 总结。"
                    )
                elif text_only_streak >= 6:
                    cm.push_coach_hint(
                        "【警告】已连续多轮未执行工具。若无法继续，请输出 FINAL: 说明当前进度。"
                    )
                continue

            text_only_streak = 0

            # 3. Execute each parsed tool step (security-gated)
            for step in tool_steps:
                action = step.action
                normalized = step.reply
                if not action or not normalized:
                    continue

                if action not in ctx.allowed_actions:
                    cm.push_coach_hint(
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
                    continue

                err = ""
                try:
                    tool_result = await ToolExecutor.execute(
                        action, normalized,
                        ctx.db, ctx.agent, sandbox,
                        ctx.skill_ids, ctx.mcp_ids,
                        ctx.httpmcp_ids, ctx.rag_ids,
                    )
                except Exception as exc:
                    tool_result = f"工具执行异常: {exc}"
                    err = f"{type(exc).__name__}: {exc}"[:400]

                state.ran_any_tool = True
                tool_call_count += 1
                result_text = tool_result or ""

                if action == "file_write" and result_text:
                    m = re.search(r"已写入\s+(\S+)", result_text)
                    if m:
                        path = m.group(1)
                        if path not in state.saved_paths:
                            state.saved_paths.append(path)
                        state.files_written += 1
                        MessageManager.append_progress(
                            state.progress_lines, f"已写入 {path}",
                        )

                cm.push_tool_result(result_text, action=action)
                await self._append_tool_step(
                    ctx, state,
                    iteration=round_no,
                    action=action,
                    title=f"[{action}]",
                    status="error" if err else "done",
                    content=err or result_text[:300],
                )

            if state.progress_lines:
                cm.set_progress_block(state.progress_lines)

            # Trim context periodically
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
            return _ret(DecisionEngine.clean_final_answer(state.last_reply))
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
    """Resolve agent config from the DB, build an AgentContext, run the loop."""
    from app.models import LLMResource, Sandbox, ChatNote
    from app.services.agent_runtime.context import AgentContext
    from app.services.agent_runtime.utils import _bound_mcp_names
    from app.services.skill_loader import load_skill_mds

    llm = db.query(LLMResource).filter(LLMResource.id == agent.llm_id).first()
    sandbox = db.query(Sandbox).filter(Sandbox.id == agent.sandbox_id).first()

    allowed = json.loads(agent.allowed_actions or "[]")
    skill_ids = json.loads(agent.skills or "[]")
    mcp_ids = json.loads(agent.mcps or "[]")

    # Auto-include required action types when corresponding bindings exist
    if mcp_ids and "mcp_tool_call" not in allowed:
        allowed.append("mcp_tool_call")
    if skill_ids:
        if "skill_read_md" not in allowed:
            allowed.append("skill_read_md")
        if "skill_run_script" not in allowed:
            allowed.append("skill_run_script")
    rag_ids = json.loads(getattr(agent, "rags", None) or "[]")
    httpmcp_ids: list[str] = []

    save_dir = workplace_dir.strip().strip("/")
    user_meta = dict(message_meta or {})

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
        skill_names=skill_names,
        mcp_names=mcp_names,
        save_dir=save_dir,
        note_content=note_content,
        user_meta=user_meta,
    )

    runtime = AgentRuntime()
    return await runtime.run(ctx)
