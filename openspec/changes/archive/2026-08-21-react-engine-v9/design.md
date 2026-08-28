# Design — react-engine-v9

## Context

动机与根因见 `proposal.md`「Why」与 `docs/exploration/react-engine-v9.md`。铁律贯穿：

- **铁律**：无任何静态硬门禁。V9 三条需求全部落在**软提示词 + 上下文卫生**上，不引入任何循环级计数器 / 阈值 / 硬停。
- 循环现状：`_run_modular` 每轮 `chat_completion(list(cm.messages), max_tokens=8192)`（`runtime.py:835-844`），`for step in tool_steps`（`runtime.py:1049`）已支持同轮多工具串行执行；结果经 `cm.push_tool_result(..., clip=6000)`（`runtime.py:1154`）入上下文。
- 裁剪现状：`fit_messages_to_context`（`llm_client.py:335-434`）预算裁剪 + `allowed_out` 封顶；`trim_tool_results` 每 8 轮只留最近 24 条工具消息（`runtime.py:1168`、`context_manager.py:24`）。
- MCP 对照：`McpSessionManager` 已有大结果落盘（`task/<ts>/mcp_result_*.json`），上下文只回显路径——R2 把这一机制推广到 READ/SHELL。

## Goals / Non-Goals

**Goals:**
- 文字任务轮数从「步数级」降到「依赖链级」（同轮批量）。
- READ/SHELL 大结果不再整段塞入上下文（落盘回显）。
- 长任务在窗口内稳定收敛，不再触发 MiniMax 2013。

**Non-Goals:**
- 不改 `_run_modular` 循环本体（同轮多工具已支持）。
- 不做 READ offset/limit 分页（D5 留后续）。
- 不改 `fit_messages_to_context` 裁剪策略（仅加余量）。
- 不动 MCP 既有大结果落盘机制。

## Decisions

### D1: 双管齐下

降轮次（R1，纯提示词）+ 控上下文（R2/R3）。两者互补，单轴不足以根治笔记梳理这类读大量文件的任务。

### D2: 落盘回显（对齐 MCP）

READ/SHELL 结果超阈值时全文落盘 `task/<ts>/`，上下文只回显「路径 + 前 N 行预览 + 总长度」。
- **为何**：MCP 已验证此模式有效；落盘判定放 `runtime.py`（有 `run_ts`/`save_dir`），在 `push_tool_result` 之前替换结果串。

### D3: 无依赖同轮

提示词引导「无依赖的独立步骤可同轮多个工具；有依赖（后步需前步结果）仍分轮」。
- **为何**：引擎已支持同轮多工具，纯粹是提示词措辞问题；无依赖才安全批量。

### D4: 阈值与范围

落盘阈值 4000 字符；范围 READ + SHELL（超长时）；SEARCH 已 cap N 条，不落盘。

### D5: READ 分页留后续

README 加 offset/limit（真正分页读大文件）是增强项，不在本 V9 硬性范围。

### D6: 铁律不变

全部软提示词 + 上下文卫生，无静态硬门禁 / 阈值。

## Risks / Trade-offs

- [风险] 同轮多工具可能让模型一次塞太多步骤、结果混在一起难处理。→ 缓解：仅「无依赖」步骤同轮；有依赖仍分轮。
- [风险] 落盘回显后模型可能不主动 READ/SEARCH 取回，导致信息丢失。→ 缓解：回显串带「前 N 行预览 + 路径」，提示词明确「按需 READ/SEARCH 取回」。
- [风险] 落盘文件堆积占用磁盘。→ 缓解：复用 `task/<ts>/` 目录，随运行目录统一清理（与 mcp_result 一致）。
- [风险] `allowed_out` 余量过大会牺牲输出长度。→ 缓解：余量温和（默认 reserve 基础上小幅收紧），只补估算误差。

## Open Questions

- 落盘阈值 4000 是否合适；预览行数 N 取值。
- `allowed_out` 安全余量具体大小。
- READ offset/limit 分页是否值得纳入后续 V10。
