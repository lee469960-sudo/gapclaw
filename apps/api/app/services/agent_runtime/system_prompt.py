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
    from sqlmodel import Session


class McpToolsCatalog(str):
    """Prompt text plus the actual per-MCP discovery outcome.

    It remains a ``str`` so existing prompt consumers retain their contract.
    """

    def __new__(cls, text: str, mcp_load_results: list[dict] | None = None):
        value = super().__new__(cls, text)
        value.mcp_load_results = list(mcp_load_results or [])
        return value


def _proactivity_hint(agent) -> str:
    """Soft clarify-vs-act instruction from agent.proactivity (1 conservative → 3 autonomous)."""
    try:
        level = int(getattr(agent, "proactivity", None) or 2)
    except (TypeError, ValueError):
        level = 2
    if level <= 1:
        return "【主动等级·保守】遇到模糊、有歧义或有风险的需求时，先向用户澄清关键点，确认后再执行。"
    if level >= 3:
        return "【主动等级·完全自主】优先直接执行推进任务，仅在确实无法继续时才向用户反问。"
    return "【主动等级·平衡】明显模糊或高风险时先澄清；其余情况直接执行推进任务。"


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
        """Assemble base system prompt from agent identity, prompt, memory, and rolling summary."""
        prompt = getattr(agent, "prompt", None) or "You are a helpful assistant."
        if not isinstance(prompt, str):
            prompt = str(prompt)
        parts: list[str] = []
        name = str(getattr(agent, "name", "") or "").strip()
        desc = str(getattr(agent, "description", "") or "").strip()
        if name or desc:
            label = f"身份：Agent「{name or '当前 Agent'}」" + (f"——{desc[:200]}" if desc else "")
            parts.append(label)
        parts.append(prompt)
        memory = getattr(agent, "memory", None)
        if memory and str(memory).strip():
            parts.append(
                f"\n\n长期规则（必须遵守，优先级最高，与任务默认口径冲突时以规则为准）:\n{str(memory).strip()}"
            )
        if summary_text and summary_text.strip():
            parts.append(f"\n\n会话滚动总结:\n{summary_text.strip()}")
        hint = _proactivity_hint(agent)
        if hint:
            parts.append(hint)
        return "\n".join(parts)

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
        httpmcp_ids: list[str] | None = None,
        save_dir: str = "",
        im_source: str = "",
    ) -> str:
        """Build the tools description block for the system prompt.

        Wraps the core _build_tools_desc logic from react_engine.py.
        """
        from app.services.agent_runtime.utils import (
            _format_mcp_tools_for_prompt,
            _get_mcp_tools_cached,
        )
        from app.models import MCP, Skill, HttpMcp

        # Resolve MCP reachability up front so the catalog tells the truth:
        # live tool names when the endpoint answers, a soft "不可达" warning when
        # it doesn't — never a hardcoded per-MCP SOP (that lives in Skill references).
        mcp_lines: list[str] = []
        mcp_load_results: list[dict] = []
        mcp_reachable = False
        if mcp_ids and "mcp_tool_call" in allowed:
            for mid in mcp_ids:
                try:
                    mcp = db.query(MCP).filter(MCP.id == mid).first()
                    if not mcp:
                        mcp_load_results.append({"mcp_id": str(mid), "status": "mcp_not_found"})
                        continue
                    tools, err = await _get_mcp_tools_cached(mcp)
                    name = mcp.name or mid
                    if err:
                        mcp_load_results.append({"mcp_id": str(mid), "status": "tools_list_failed"})
                        mcp_lines.append(
                            f"- mcp {name}: 端点不可达 / tools/list 失败（{err[:160]}）。"
                            "本轮请勿反复重试 MCP 工具；确需该数据源时直接 `FINAL` 说明无法连接，"
                            "由用户检查 MCP 服务后重试。"
                        )
                    elif tools:
                        mcp_load_results.append({"mcp_id": str(mid), "status": "catalog_loaded"})
                        mcp_reachable = True
                        mcp_lines.extend(_format_mcp_tools_for_prompt(name, tools))
                    else:
                        mcp_load_results.append({"mcp_id": str(mid), "status": "catalog_empty"})
                        mcp_lines.append(
                            f"- mcp {name}: 已绑定，但 tools/list 返回空（该地址可能不是 MCP 端点）。"
                        )
                except Exception:
                    logger.warning("Failed to load MCP tools for mid=%s", mid, exc_info=True)
                    mcp_load_results.append({"mcp_id": str(mid), "status": "connection_failed"})
                    mcp_lines.append(f"- mcp (id={mid[:12]}): 连接失败，暂时不可用。")

        # HttpMcp catalog: local tool list (no network call), flattened to name+desc+args.
        httpmcp_lines: list[str] = []
        if httpmcp_ids and "httpmcp_call" in allowed:
            for hid in httpmcp_ids:
                try:
                    hm = db.query(HttpMcp).filter(HttpMcp.id == hid).first()
                    if not hm:
                        continue
                    tools = hm._tools() or []
                    name = hm.name or hid
                    if not tools:
                        httpmcp_lines.append(f"- httpmcp {name}: 已绑定，但未配置任何工具。")
                        continue
                    httpmcp_lines.append(
                        f"- httpmcp {name}: 使用下列真实工具名，格式 HTTPMCP: <工具名> {{json vars}}"
                    )
                    for t in tools[:30]:
                        if not isinstance(t, dict) or not t.get("name"):
                            continue
                        tname = str(t["name"]).strip()
                        tdesc = re.sub(r"\s+", " ", str(t.get("description") or "")).strip()[:80]
                        targs = t.get("args") if isinstance(t.get("args"), list) else []
                        arg_names = []
                        for a in targs:
                            if isinstance(a, dict):
                                an = a.get("key") or a.get("name") or a.get("label") or a.get("arg")
                            else:
                                an = a
                            if an:
                                arg_names.append(str(an).strip())
                        arg_bit = f" args=[{', '.join(arg_names)}]" if arg_names else ""
                        suffix = f"  # {tdesc}" if tdesc else ""
                        httpmcp_lines.append(f"  - HTTPMCP: {tname} {{...}}{suffix}{arg_bit}")
                except Exception:
                    logger.warning("Failed to load HttpMcp tools for hid=%s", hid, exc_info=True)
                    httpmcp_lines.append(f"- httpmcp (id={hid[:12]}): 加载失败，暂时不可用。")

        has_shell = "shell" in allowed
        if save_dir:
            path_rule = (
                f"【路径规则】过程产物全部写入 `task/<毫秒时间戳>/`（JSON/CSV 文本）；"
                + (
                    f"最终交付的 xlsx：用 SHELL（pandas/openpyxl）写入当前目录 `{save_dir}/`；"
                    if has_shell else
                    "最终交付文件用 WRITE 写入当前目录；"
                ) +
                f"禁止 WRITE 直接写 *.xlsx/*.pdf 等二进制；禁止把分片堆到当前目录；禁止再套 `workplace/`。"
            )
            write_example = "WRITE: task/<毫秒时间戳>/final_data.json"
        else:
            path_rule = (
                "【路径规则】过程产物全部写入 `task/<毫秒时间戳>/`（JSON/CSV 文本）；"
                + (
                    "最终交付的 xlsx：用 SHELL（pandas/openpyxl）写入当前目录（工作区根）；"
                    if has_shell else
                    "最终交付文件用 WRITE 写入当前目录；"
                ) +
                "禁止 WRITE 直接写 *.xlsx/*.pdf 等二进制；禁止把分片堆到根目录；禁止再套 `workplace/`。"
            )
            write_example = "WRITE: task/<毫秒时间戳>/final_data.json"
        lines = [
            "【重要】每次从行首输出工具调用，禁止使用 XML/tool_call 格式。"
            "无依赖的独立步骤可同轮输出多个工具调用（如多个 READ/SEARCH、多个独立 SHELL、"
            "一次 describe 多个 view）；一旦下一步依赖上一步结果，就停下来等观察后再继续（有依赖仍分轮）。",
            "【批量工具】适合一次性读取多个文件/范围、多个互不依赖查询或多个同域诊断时，优先输出一行 "
            '`BATCH: {"mode":"parallel","children":[{"id":"r1","reply":"READ: a.py"},{"id":"r2","reply":"READ: b.py"}]}`；'
            "有顺序依赖但不需要中间 LLM 推理时用 `mode:\"sequence\"`；多文件 patch 必须用 "
            '`mode:"transaction"` 且 child 只能是 `PATCH:`，禁止把 `WRITE:` 全文件覆盖放进 transaction。'
            "不要跨安全域/MCP/权限边界混批；需要观察上一步结果再决定下一步时不要 batch。"
            "多个相关 SHELL 不要拆成多个 parallel child；优先合成一条完整脚本/命令，确需多条时用 sequence。",
            (
                "【格式】SHELL:/WRITE:/FINAL: 必须单独占一行，不要在说明文字同一行内夹杂工具指令。"
                if has_shell
                else "【格式】WRITE:/FINAL: 必须单独占一行，不要在说明文字同一行内夹杂工具指令。"
            ),
            path_rule,
            "【文件名规范】写入/重定向的文件名只用字母、数字、中文、下划线、点、连字符；"
            "禁止把 shell 特殊字符（[ ] * ? < > | & ; $ ( ) 及引号）写进文件名或 `>` 重定向目标，"
            "否则会生成 `[`、`*` 之类无法识别的乱码文件。",
            "【终止】每轮要么调用工具（可批量互不依赖的调用），要么输出一行 `FINAL: <总结>` 表示任务完成。"
            "只输出文字、既不调工具也不 FINAL 会导致重复追问；任务无法继续时也要 `FINAL:` 说明当前进度。",
            "【FINAL 排版】FINAL 用 Markdown 排版（标题/列表/表格）便于阅读；"
            "交付文件路径用反引号包裹（如 `output.xlsx`）以渲染为可下载卡片。",
            "【附件标注】需把产出文件作为附件发给用户时，在 `FINAL:` 行末尾标注 `attach=<路径1,路径2>`"
            "（逗号分隔、路径相对 workplace）；也可单独输出一行 `ATTACH: <路径1,路径2>`。"
            "未标注时由平台自动兜底挑选最新文件。",
            "【策略】先输出 PLAN:（目标/步骤/完成标准）再按步骤调用工具；引擎会记住并持续回显你的 PLAN，"
            "后续可随时输出新的 PLAN: 更新。对照完成标准后输出 FINAL。优先阅读已绑定 Skill。"
            "把探索结论（已确认的 view→字段映射、关键口径）蒸馏进 PLAN，不要在上下文里依赖易失的原始观测。",
            "【规划·子任务】长任务请输出结构化 PLAN：每行一个子任务，用 `- [x]` 标已完成、`- [ ]` 标待办。"
            "子任务文本只写目标、不写工具名（正例 `- [ ] 找到白名单视图`，反例 `- [ ] list_ads_views → 找白名单视图`），"
            "工具调用要单独用协议行（MCP:/SHELL: 等）发出，写在 PLAN 里不会被当成调用。"
            "完成一个子任务后重新输出完整 PLAN（已完成的保持 `[x]`）；引擎会记住并回显，"
            "多子任务（≥2 项）会被持久化，中断后可从断点续跑。",
        ]
        if mcp_reachable:
            lines.append(
                "【导出/报表】数据导出与报表的详细口径（FINAL 要素、xlsx 生成、依赖自装、多 MCP 下按工具归属选 SOP）"
                "见已绑定 Skill 的 references；引擎目录只列真实工具名、描述与 required 参数。"
                "未绑定对应 Skill 时，按通用流程：先 list/describe 类工具确认目标资源与字段，再 query 取数，"
                "用 SHELL(pandas) 生成 xlsx 交付。"
                "【资源映射】list/describe/query 原始结果可能被截断或落盘（过大时写入 mcp_result_*.json）；"
                "确认的 view→字段/口径映射必须蒸馏进 PLAN 或落盘文件，不要逐个 describe 大量资源——"
                "先按字段名/口径定位候选，再 describe 确认。"
                "【取数效率】拉大批量数据时优先用日期区间/聚合谓词一次取全，不要按 uid 逐条 query；"
                "互不依赖的 SQL / 查询必须同一轮批量输出，禁止一轮一条（依赖上一步结果时先观察再继续）；"
                "物化落盘的 mcp_result_*.json 用 SHELL + pandas 后处理，避免反复 MCP 往返。"
            )
            lines.append(
                "【分页补齐】工具目录里标了「分页参数」的查询工具支持 offset/limit 翻页；"
                "结果若被截断（返回行数 ≈ 所设 limit），同一轮用 offset 追加剩余页一次取全，"
                "不要等到下一轮再补。"
                "【查询效率】写查询前先收窄范围：优先用 WHERE/LIMIT 缩小结果集、避免全表扫描；"
                "聚合（COUNT/SUM/分组等）尽量在 SQL 侧完成、少拉明细；"
                "在多个可选查询方案中，按「总等待时间」选最优（少轮次、小结果集优先）。"
            )
            lines.append(
                "【数据视图目录】任务上下文中若出现「数据视图目录」（可用视图名清单 + 已确认 view→字段/口径），"
                "先按语义在清单中定位候选视图，只对命中的 top 候选调用 describe 类工具确认字段，"
                "不要逐个 describe 全部视图；清单缺失或明显过时时再按需用 list 类工具重新列举。"
            )
        lines += [
            "【发文件到消息渠道】用户要把已有报表/xlsx 发到 TG/飞书/钉钉等绑定渠道时："
            "优先使用工作目录已有文件（含 task/_stale_/），禁止为此再调用 MCP 数据源查询；"
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
            lines.append(
                "【无状态】每次 SHELL 都是全新进程，变量/已加载的 dataframe 不会跨轮保留；"
                "多步 Python 请写成完整脚本 WRITE 到 /tmp/ 再 SHELL 执行，不要逐行输出赋值片段。"
            )
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
        if "file_search_replace" in allowed:
            lines.append("- patch: PATCH: <path>\\n<old>\\n<new>")
        if "skill_read_md" in allowed or "skill_run_script" in allowed:
            for sid in skill_ids:
                sk = db.query(Skill).filter(Skill.id == sid).first()
                if sk:
                    lines.append(
                        f"- skill {sk.name}: RUN_SKILL: {sk.name} | SKILL_MD: {sk.name} | "
                        f"包内文档 SKILL_MD: {sk.name} references/<file>.md（不要用 READ）"
                    )
        if mcp_ids and "mcp_tool_call" not in allowed:
            lines.append(
                "- 【软提示】Agent 已绑定 MCP，但未开启 mcp_tool_call 权限；"
                "如需拉数请在 Agent 允许操作中启用 MCP。"
            )
        if "mcp_tool_call" in allowed:
            lines.extend(mcp_lines)
        if "httpmcp_call" in allowed:
            lines.extend(httpmcp_lines)
        if "file_search" in allowed:
            lines.append("- file_search: SEARCH: <query>（递归搜索 workplace 文本文件内容，大小写不敏感，返回 file:line: 片段）")
        if "code_read" in allowed:
            lines.append("- code_read(path): 读取冻结 Code Workspace 中允许路径内的文本文件")
        if "code_search" in allowed:
            lines.append("- code_search(query, path): 在允许路径内搜索文本")
        if "code_edit" in allowed:
            lines.append("- code_edit(path, content): 以完整文本替换允许路径内的文件")
        if "code_test" in allowed:
            lines.append("- code_test(test_index): 运行冻结验证计划中的指定测试，不接受任意命令")
        if "code_shell" in allowed:
            lines.append("- code_shell(command): 仅运行有效策略冻结的完整命令，禁止 shell 元字符和命令组合")
        if "code_git" in allowed:
            lines.append("- code_git(operation): 只读 Git 查询，operation 仅限 status/diff/log")
        if "rag_query" in allowed and rag_ids:
            lines.append("- rag: RAG: <query>")
        if "recall" in allowed:
            lines.append("- recall: RECALL: <关键词>（检索本轮上下文的归档，需历史信息时调用；区别于 MCP 的 recall 工具）")
        lines.append("- 大结果落盘：READ/SHELL 结果过大（超 4000 字符）或 MCP 查询返回超大结果时，引擎会自动把全量结果写入 task/<ts>/（如 shell_result_N.txt、read_result_N.txt、mcp_result_N.json），上下文只回显「路径 + 前几行预览 + 总长度」；请用 READ/SEARCH 按需取回所需片段，不要在上下文粘贴原始大结果。")
        lines.append("- 去重复用：重复的 READ/SHELL/SEARCH（同一路径 / 同一命令 / 同一 query）会命中缓存并回显「该结果已缓存」指针；请直接引用之前结果，不要重读 / 重跑相同动作。")
        lines.append("- done: 任务完成时调用 done(answer=…) 或输出 `FINAL: <answer>`（二者等效，二选一）")
        return McpToolsCatalog("\n".join(lines), mcp_load_results)

    @staticmethod
    def build_tool_schemas(
        allowed_actions: list[str], *, mcp_configured: bool = True,
    ) -> list[dict]:
        """Build OpenAI-compatible function schemas for the fixed meta-tool set.

        Only declares tools the agent is allowed and configured to call;
        `done` is always declared as the completion signal. Tool names here
        must match the tool_calls normalization in
        ``llm_client.extract_chat_response_text``.
        """
        allowed = set(allowed_actions or [])

        def fn(name: str, desc: str, props: dict, required: list[str]) -> dict:
            return {
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": {
                        "type": "object",
                        "properties": props,
                        "required": required,
                    },
                },
            }

        tools: list[dict] = []
        if "shell" in allowed:
            tools.append(fn(
                "shell", "Run a POSIX /bin/sh command in the sandbox.",
                {"cmd": {"type": "string", "description": "The shell command to execute."}},
                ["cmd"],
            ))
        if "file_write" in allowed:
            tools.append(fn(
                "file_write", "Write text content to a workplace file (auto-creates parent dirs; text only).",
                {
                    "path": {"type": "string", "description": "Workplace-relative file path."},
                    "content": {"type": "string", "description": "Full text content to write."},
                },
                ["path", "content"],
            ))
        if "file_read" in allowed:
            tools.append(fn(
                "file_read", "Read a file's content, or list a directory's entries.",
                {"path": {"type": "string", "description": "Workplace-relative path."}},
                ["path"],
            ))
        if "file_search" in allowed:
            tools.append(fn(
                "file_search", "Search text files under the workplace for a case-insensitive substring.",
                {"query": {"type": "string", "description": "Substring to search for."}},
                ["query"],
            ))
        if "file_search_replace" in allowed:
            tools.append(fn(
                "file_search_replace", "Replace the first occurrence of `old` with `new` in a file.",
                {
                    "path": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                },
                ["path", "old", "new"],
            ))
        if "code_read" in allowed:
            tools.append(fn(
                "code_read", "Read a text file from the policy-scoped Code Workspace.",
                {"path": {"type": "string"}}, ["path"],
            ))
        if "code_search" in allowed:
            tools.append(fn(
                "code_search", "Search text under an allowed Code Workspace path.",
                {"query": {"type": "string"}, "path": {"type": "string"}}, ["query"],
            ))
        if "code_edit" in allowed:
            tools.append(fn(
                "code_edit", "Replace a policy-allowed Code Workspace file with full text content.",
                {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"],
            ))
        if "code_test" in allowed:
            tools.append(fn(
                "code_test", "Run one command from the frozen validation plan by index.",
                {"test_index": {"type": "integer", "minimum": 0}}, ["test_index"],
            ))
        if "code_shell" in allowed:
            tools.append(fn(
                "code_shell", "Run one exact command approved by the frozen Code policy.",
                {"command": {"type": "string"}}, ["command"],
            ))
        if "code_git" in allowed:
            tools.append(fn(
                "code_git", "Run a read-only Git status, diff or log query.",
                {"operation": {"type": "string", "enum": ["status", "diff", "log"]}}, ["operation"],
            ))
        if "mcp_tool_call" in allowed and mcp_configured:
            tools.append(fn(
                "mcp_tool_call", "Call a bound MCP tool by name.",
                {
                    "tool_name": {"type": "string", "description": "MCP tool name."},
                    "arguments": {"type": "object", "description": "Tool arguments object."},
                },
                ["tool_name"],
            ))
            tools.append(fn(
                "mcp_route_request", "Request one additional MCP capability when selected MCPs are insufficient.",
                {"need": {"type": "string", "description": "Missing capability and why it is needed."}},
                ["need"],
            ))
        if "httpmcp_call" in allowed:
            tools.append(fn(
                "httpmcp_call", "Call a bound HttpMcp (HTTP request proxy) tool by name.",
                {
                    "tool_name": {"type": "string", "description": "HttpMcp tool name."},
                    "arguments": {"type": "object", "description": "Tool arguments object."},
                },
                ["tool_name"],
            ))
        if "rag_query" in allowed:
            tools.append(fn(
                "rag_query", "Search the bound RAG corpus.",
                {"query": {"type": "string"}},
                ["query"],
            ))
        if "skill_read_md" in allowed:
            tools.append(fn(
                "skill_read_md",
                "Read a bound skill's SKILL.md or a markdown file inside that skill package.",
                {
                    "skill_id": {"type": "string", "description": "Bound skill name or id."},
                    "path": {
                        "type": "string",
                        "description": (
                            "Optional skill-package relative path such as "
                            "references/report-sop.md. Do not use workplace READ."
                        ),
                    },
                },
                ["skill_id"],
            ))
        if "skill_run_script" in allowed:
            tools.append(fn(
                "skill_run_script", "Run a bound skill script by name.",
                {"name": {"type": "string"}},
                ["name"],
            ))
        if "recall" in allowed:
            tools.append(fn(
                "recall", "Search this run's archived context by keyword.",
                {"query": {"type": "string"}},
                ["query"],
            ))
        tools.append(fn(
            "done", "Signal task completion with the final answer.",
            {"answer": {"type": "string", "description": "The final Markdown answer."}},
            ["answer"],
        ))
        return tools

    @staticmethod
    def build_minimal_tools_desc(
        *,
        save_dir: str = "",
        allowed_actions: list[str] | None = None,
        mcp_ids: list[str] | None = None,
        db: Session | None = None,
    ) -> str:
        """Build a minimal tools catalog as a fallback when the full catalog fails.

        Only includes essential tools (READ, WRITE, SHELL, FINAL) without MCP details.
        When mcp_ids are provided, includes a minimal MCP hint so the LLM knows MCP is available.
        """
        actions = set(allowed_actions or [])
        lines = [
            "【可用工具·精简模式】",
            "【格式】每次从行首输出工具调用；可批量输出互不依赖的调用，依赖上一步结果时停下来等观察。",
            "【批量工具】互不依赖的同域读/查/诊断优先用一行 "
            '`BATCH: {"mode":"parallel","children":[{"id":"r1","reply":"READ: a.py"},{"id":"r2","reply":"READ: b.py"}]}`；'
            "有顺序依赖但无需中间推理用 `mode:\"sequence\"`；多文件 patch 用 `mode:\"transaction\"` 且只允许 `PATCH:` child。"
            "不要跨安全域/MCP/权限边界混批。多个相关 SHELL 优先合成一条完整脚本/命令，确需多条时用 sequence。",
        ]
        if save_dir:
            lines.append(
                f"【路径规则】最终交付文件写入 `{save_dir}/`；过程产物写入 `task/<毫秒时间戳>/`。"
            )
        if "shell" in actions:
            lines.append("- SHELL: <POSIX /bin/sh command>（禁止混入思考或解释文字）")
        if "file_read" in actions:
            lines.append("- READ: <path>（文件可读内容；目录可列出条目；READ: workplace 根目录）")
        if "file_write" in actions:
            lines.append("- WRITE: <path>\\n<content>（自动创建父目录，无需 mkdir）")
        if "shell" not in actions and {"file_read", "file_write"} & actions:
            lines.append(
                "【无 Shell 模式】目录浏览和文本落盘仅使用 READ/WRITE；"
                "不要输出 SHELL，也不要因缺少 mkdir/ls 中止任务。"
            )
        if mcp_ids and "mcp_tool_call" in actions:
            try:
                from app.models import MCP
                for mid in mcp_ids:
                    mcp = db.query(MCP).filter(MCP.id == mid).first() if db else None
                    name = (mcp.name if mcp else mid[:12]) if (mcp or mid) else "unknown"
                    lines.append(
                        f"- mcp {name}: MCP 工具（MCP: <tool_name> {{args}}）。"
                        "请参考已绑定 MCP 的文档使用正确的工具名和参数。"
                    )
            except Exception:
                lines.append("- mcp: MCP 工具已绑定（详情暂不可用，请重试或检查 MCP 连接）")
        lines.append("- FINAL: <answer>")
        return "\n".join(lines)

    # ---- Static hint / coach builders ----

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
    def build_skill_snapshot(skill_mds: list[tuple[str, str]]) -> str | None:
        """Build a skill snapshot text from skill markdown tuples."""
        if not skill_mds:
            return None
        return "\n".join(f"{n}\n{m}" for n, m in skill_mds)
