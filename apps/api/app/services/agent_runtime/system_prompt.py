"""SystemPromptBuilder — system prompt and coaching message construction.

Builds the core system identity, model understanding, tools description,
and various coaching hints injected during agent loop execution.
All methods are static or take explicit parameters.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.agent.models import Agent
    from app.models.mcp import MCP
    from app.models.skill import Skill
    from app.services.task_policy import TaskPolicy
    from sqlmodel import Session


class SystemPromptBuilder:
    """Builds system messages for the ReAct agent loop.

    Stateless — all inputs are passed explicitly.
    """

    # ---- Agent identity helpers ----

    @staticmethod
    def agent_identity_label(agent: Agent | None) -> str:
        """Human-readable agent label for use in prompts."""
        if not agent:
            return "当前 Agent"
        name = str(getattr(agent, "name", "") or "").strip()
        return f"Agent「{name}」" if name else "当前 Agent"

    # ---- Base system prompt assembly ----

    @staticmethod
    def build_system_base(
        agent: Agent,
        *,
        summary_text: str = "",
    ) -> str:
        """Assemble base system prompt from agent.prompt, memory, and rolling summary."""
        prompt = getattr(agent, "prompt", None) or "You are a helpful assistant."
        if not isinstance(prompt, str):
            prompt = str(prompt)
        parts = [prompt]
        memory = getattr(agent, "memory", None)
        if memory and str(memory).strip():
            parts.append(f"\n\n长期记忆:\n{str(memory).strip()}")
        if summary_text and summary_text.strip():
            parts.append(f"\n\n会话滚动总结:\n{summary_text.strip()}")
        return "\n".join(parts)

    # ---- Model understanding (identity + task routing) ----

    @staticmethod
    def build_model_understanding(
        agent: Agent | None,
        task_policy: TaskPolicy,
    ) -> str:
        """Stable model-side contract: understand first, engine executes contracts."""
        label = SystemPromptBuilder.agent_identity_label(agent)
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

    # ---- Conversational (tool-free) path ----

    @staticmethod
    def build_conversational_system(agent: Agent | None) -> str:
        """System prompt for tool-free single-turn chat (no PLAN/MCP/FINAL protocol)."""
        label = SystemPromptBuilder.agent_identity_label(agent)
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

    @staticmethod
    def build_light_agent_reply(agent: Agent | None, user_message: str) -> str:
        """Short user-facing fallback reply; never exposes the raw base prompt."""
        label = SystemPromptBuilder.agent_identity_label(agent)
        um = (user_message or "").strip()
        if any(k in um for k in ("你好", "hello", "hi", "在吗", "你是谁", "帮助")):
            return f"你好！我是{label}，有什么可以帮你的？"
        if len(um) <= 100:
            return f"收到你的消息。我是{label}，如需执行任务请补充具体要求。"
        return f"已收到你的详细消息，我是{label}，将据此处理。如需进一步操作请直接说明。"

    # ---- Tools description (async) ----

    @staticmethod
    async def build_tools_desc(
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
        """Build the tools description block for the system prompt.

        Wraps the core _build_tools_desc logic from react_engine.py.
        """
        from app.services.agent_runtime.utils import (
            _format_mcp_tools_for_prompt,
            _get_mcp_tools_cached,
        )
        from app.models import MCP, Skill

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
            write_example = "WRITE: task/<毫秒时间戳>/final_data.json"
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
                "2) 必须输出 PLAN:（目标/视图/筛选/输出列/需要资源/预算/SHELL 步骤）；\n"
                "3) 限量 fetch：仅拉列意图/白名单资源；平台写入 task/page_N.json；"
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
        if not has_shell:
            export_strategy = export_strategy.replace(
                "SHELL 步骤", "平台交付步骤"
            ).replace(
                "优先 SHELL 写当前目录交付物",
                "由平台生成并校验当前目录交付物",
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
                try:
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
                except Exception:
                    logger.warning(
                        "Failed to load MCP tools for mid=%s", mid, exc_info=True,
                    )
                    lines.append(
                        f"- mcp (id={mid[:12]}): 连接失败，暂时不可用。"
                        "请检查 MCP 服务状态后重试。"
                    )
        if "httpmcp_call" in allowed:
            lines.append('- httpmcp: HTTPMCP: <id> {"tool":"<工具名>", ...变量}')
        if "rag_query" in allowed and rag_ids:
            lines.append("- rag: RAG: <query>")
        if "self_ask" in allowed:
            lines.append("- think: THINK: <thought>")
        lines.append("- done: FINAL: <answer>")
        return "\n".join(lines)

    # ---- Static hint / coach builders ----

    @staticmethod
    def build_save_dir_hint(save_dir: str) -> str:
        """Build the save-directory system hint."""
        return (
            f"【保存目录】用户已选中 `{save_dir}` 作为当前目录："
            f"仅最终交付文件写入 `{save_dir}/`；过程产物写入 `task/<毫秒时间戳>/`。"
        )

    @staticmethod
    def build_workplace_listing_hint(listing: str) -> str:
        """Build the workplace directory listing system hint."""
        lines = listing.splitlines()
        truncated = "\n".join(lines[:80]) + "\n…(目录列表已截断)" if len(lines) > 80 else listing
        return (
            "【当前工作目录】以下列表与左侧文件面板一致（API 持久化目录）。"
            "查看文件请优先用 READ: <相对路径>，不要用 SHELL: ls /workplace 判断文件是否存在。\n"
            f"{truncated}"
        )

    @staticmethod
    def build_prior_exports_hint(prior_paths: list[str]) -> str:
        """Build the recent exports hint for system prompt."""
        return (
            "【近期已导出文件】"
            + "、".join(f"`{p}`" for p in prior_paths)
            + "。若用户只要把已有报表发到 Telegram/TG，禁止再调用 query_ads_view / describe_ads_view；"
            "直接 FINAL 说明由平台推送，或提示用户说「发给tg」。"
        )

    @staticmethod
    def build_whitelist_hint(resource_names: list[str]) -> str:
        """Build the resource whitelist hint for need-based queries."""
        return (
            "【按需拉取白名单】优先 query view/resource ∈ {"
            + ", ".join(resource_names)
            + "}；勿默认全量拉取/分页全表。"
            "若 list/describe 发现更合适的资源名，以目录为准。"
        )

    @staticmethod
    def build_tools_header(tools_desc: str) -> str:
        """Truncate and wrap tools description into the system message."""
        desc = tools_desc
        if len(desc) > 12000:
            desc = desc[:11000] + "\n…(工具说明已截断)"
        return f"可用工具:\n{desc}"

    @staticmethod
    def build_skill_snapshot(skill_mds: list[tuple[str, str]]) -> str | None:
        """Build a skill snapshot text from skill markdown tuples."""
        if not skill_mds:
            return None
        return "\n".join(f"{n}\n{m}" for n, m in skill_mds)

    # ---- Summary clamping ----

    @staticmethod
    def clamp_summary_text(text: str, max_chars: int) -> str:
        """Truncate rolling summary text to max_chars, preferring complete sentences."""
        t = (text or "").strip()
        if len(t) <= max_chars:
            return t
        cut = t[:max_chars].rstrip()
        # Try to break at last sentence end
        for sep in ("\n\n", "\n", "。", ". ", "；", "; "):
            idx = cut.rfind(sep)
            if idx > max_chars // 2:
                cut = cut[: idx + len(sep)].rstrip()
                break
        return cut + "\n…(总结已截断)"

    @staticmethod
    def summary_max_chars(agent: Agent) -> int:
        """Compute max chars for rolling summary based on agent config."""
        history_len = int(getattr(agent, "history_length", None) or 30)
        base = 2000
        if history_len > 50:
            base = 3000
        elif history_len > 20:
            base = 2000
        else:
            base = 1200
        return min(base, 4000)
