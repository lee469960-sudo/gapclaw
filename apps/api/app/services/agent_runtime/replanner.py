"""Replanner — dynamic plan modification when execution diverges from expectations.

When the Verifier detects that a step's goal was not achieved, the Replanner:
  1. Decides whether a replan is warranted (should_replan)
  2. Generates a coaching hint for the LLM (generate_replan_hint)
  3. Parses the LLM's revised plan (parse_replan_reply)
  4. Merges completed steps with the new plan (merge_replan)

Key design principle: Engine coaches, LLM decides. The Replanner does NOT
generate plans itself — it provides structured context to help the LLM
adjust the plan intelligently.

Triggers:
  - Verifier reports next_action == "replan"
  - Same tool/params fail repeatedly (stalling)
  - New schema information discovered (e.g., view not found → alternative exists)
  - Budget approaching limit (must consolidate remaining steps)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.agent_runtime.types import PlanStep, VerificationResult


@dataclass
class ReplanContext:
    """Context passed to the Replanner for decision-making."""

    failed_step: PlanStep | None = None
    verification: VerificationResult | None = None
    remaining_steps: list[PlanStep] = field(default_factory=list)
    completed_step_ids: set[str] = field(default_factory=set)
    available_tools: list[str] = field(default_factory=list)
    budget_remaining: int = 100
    consecutive_failures: int = 0
    plan_version: int = 1
    task_goal: str = ""


class Replanner:
    """Dynamically modifies the remaining plan when assumptions change.

    Stateless — all methods take explicit ReplanContext or parameters.
    """

    # ---- Decision: should we replan? ----

    @staticmethod
    def should_replan(
        verification: VerificationResult | None,
        *,
        consecutive_failures: int = 0,
        budget_remaining: int = 0,
        step_retry_count: int = 0,
        max_retries: int = 3,
        same_action_count: int = 0,
    ) -> bool:
        """Determine if a replan is warranted based on current state.

        Returns True when:
          - Verifier explicitly says "replan"
          - Same action executed > 3 times (loop detection)
          - Step retried beyond max_retries
          - Budget critically low (< 20% remaining)
          - 3+ consecutive failures
        """
        if verification and verification.next_action == "replan":
            return True

        if same_action_count > 3:
            return True

        if step_retry_count >= max_retries:
            return True

        if budget_remaining <= 0:
            return True

        if consecutive_failures >= 3:
            return True

        return False

    # ---- Coach hint generation ----

    @staticmethod
    def generate_replan_hint(ctx: ReplanContext) -> str:
        """Generate a coaching hint that asks the LLM to revise the plan.

        The hint is injected via ContextManager.push_coach_hint() and
        appears in the system context for the next LLM call.

        The hint describes WHAT went wrong and WHAT to consider, but
        does NOT prescribe a specific new plan — the LLM decides.
        """
        lines = ["【需要调整计划】"]

        # What failed
        if ctx.failed_step:
            lines.append(
                f"步骤「{ctx.failed_step.goal}」未达成 (retry={ctx.failed_step.retry_count}/{ctx.failed_step.max_retries})。"
            )
        if ctx.verification:
            lines.append(f"验证结果: {ctx.verification.reason}")
            if ctx.verification.missed_criteria:
                lines.append(f"未满足条件: {', '.join(ctx.verification.missed_criteria)}")
            if ctx.verification.suggestion:
                lines.append(f"建议: {ctx.verification.suggestion}")

        # What's done
        if ctx.completed_step_ids:
            lines.append(f"已完成步骤: {', '.join(sorted(ctx.completed_step_ids))}")

        # What remains
        if ctx.remaining_steps:
            remaining_desc = "\n".join(
                f"  - [{s.id}] {s.goal} (status={s.status})"
                for s in ctx.remaining_steps
            )
            lines.append(f"剩余步骤:\n{remaining_desc}")
        else:
            lines.append("剩余步骤: (无)")

        # Budget
        if ctx.budget_remaining < 10:
            lines.append(
                f"⚠️ 预算即将耗尽 (剩余 {ctx.budget_remaining} 次工具调用)。"
                "请精简剩余步骤，优先保障核心目标。"
            )
        elif ctx.budget_remaining < 30:
            lines.append(f"预算剩余 {ctx.budget_remaining} 次调用，请合理分配。")

        # Available tools
        if ctx.available_tools:
            lines.append(f"可用工具: {', '.join(ctx.available_tools[:15])}")

        # Action instruction
        lines.append("")
        lines.append(
            "请根据当前情况输出修订后的 PLAN: 块，包含：\n"
            "1) 保留已完成的步骤\n"
            "2) 对于失败步骤，改为替代方案或不同工具\n"
            "3) 调整后续步骤顺序和依赖\n"
            "4) 更新预算分配\n\n"
            "PLAN 格式:\n"
            "PLAN:\n"
            "- [step-id] 目标描述 | 验收条件 | 预算\n"
            "...\n"
            "已完成的步骤不要重复执行。"
        )

        return "\n".join(lines)

    # ---- Plan parsing ----

    @staticmethod
    def parse_replan_reply(
        reply: str,
        current_steps: list[PlanStep],
    ) -> list[PlanStep] | None:
        """Parse a PLAN: block from an LLM reply into revised PlanStep list.

        Returns None if no valid PLAN block found (keeping current plan).
        """
        from app.services.agent_runtime.types import PlanStep

        # Find PLAN block
        plan_match = re.search(
            r"(?:^|\n)PLAN\s*[:：](.*?)(?:\n(?:FINAL|MCP|SHELL|WRITE|READ|$)|\Z)",
            reply,
            re.DOTALL | re.IGNORECASE,
        )
        if not plan_match:
            return None

        plan_text = plan_match.group(1).strip()
        lines = plan_text.splitlines()

        new_steps: list[PlanStep] = []
        for i, line in enumerate(lines):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Parse step line: "- [id] goal | criteria | budget"
            step_match = re.match(
                r"[-*]\s*\[?([^\]]+)\]?\s*(.+)", line
            )
            if not step_match:
                # Try simpler format: "- goal description"
                step_id = f"step-{i + 1}"
                goal = line.lstrip("-* ").strip()
                if not goal:
                    continue
            else:
                step_id = step_match.group(1).strip()
                goal = step_match.group(2).strip()

            # Split by | for criteria
            parts = [p.strip() for p in goal.split("|")]
            goal_text = parts[0] if parts else goal
            criteria = parts[1:]

            new_steps.append(
                PlanStep(
                    id=step_id,
                    goal=goal_text,
                    description=goal_text,
                    acceptance_criteria=criteria if criteria else [],
                    plan_version=current_steps[0].plan_version + 1 if current_steps else 2,
                )
            )

        return new_steps if new_steps else None

    # ---- Merge completed + new steps ----

    @staticmethod
    def merge_replan(
        old_steps: list[PlanStep],
        new_steps: list[PlanStep],
        *,
        completed_step_ids: set[str],
    ) -> list[PlanStep]:
        """Merge old plan with new: keep completed steps, replace remaining.

        Strategy:
          1. Keep all steps whose ID is in completed_step_ids (already done)
          2. For steps NOT in completed_step_ids, use new_steps if available
          3. If new_steps references a completed step ID, skip it
          4. Mark version bump on all remaining steps
        """
        if not new_steps:
            return old_steps

        # Build final step list
        merged: list[PlanStep] = []

        # Phase 1: Keep completed steps (preserve original order from old_steps)
        seen_ids: set[str] = set()
        for s in old_steps:
            if s.id in completed_step_ids and s.id not in seen_ids:
                merged.append(s)
                seen_ids.add(s.id)

        # Phase 2: Add new steps (excluding ones that reference completed work)
        new_ids_seen: set[str] = set()
        for ns in new_steps:
            if ns.id in completed_step_ids:
                continue  # Already done
            if ns.id in new_ids_seen:
                # Append suffix to avoid ID collision
                ns.id = f"{ns.id}-v{ns.plan_version}"
            new_ids_seen.add(ns.id)
            ns.status = "pending"
            ns.retry_count = 0
            merged.append(ns)

        return merged

    # ---- Simple hint for non-replan scenarios ----

    @staticmethod
    def generate_retry_hint(
        verification: VerificationResult,
        *,
        tool_name: str = "",
    ) -> str:
        """Generate a lightweight retry hint (not a full replan).

        Used when Verifier says "retry" — coach the LLM to fix params
        or try a slightly different approach, without full replanning.
        """
        lines = [
            f"【执行反馈】上一步未达到预期: {verification.reason}",
        ]
        if verification.suggestion:
            lines.append(f"建议: {verification.suggestion}")
        if verification.missed_criteria:
            lines.append(f"未满足: {', '.join(verification.missed_criteria)}")
        lines.append("请修正参数或调整方式后重试，无需重新 PLAN。")
        return "\n".join(lines)

    @staticmethod
    def generate_budget_warning_hint(
        budget_remaining: int,
        *,
        total_budget: int = 100,
    ) -> str:
        """Generate a budget warning hint for context injection."""
        ratio = budget_remaining / max(total_budget, 1)
        if ratio < 0.1:
            urgency = "【紧急】预算即将耗尽，请立即输出 FINAL 结束任务。"
        elif ratio < 0.2:
            urgency = "【警告】预算不足 20%，请精简步骤，优先完成核心目标后 FINAL。"
        else:
            urgency = "【提示】预算有限，请合理分配剩余工具调用。"
        return f"{urgency}\n剩余工具调用次数: {budget_remaining}/{total_budget}"
