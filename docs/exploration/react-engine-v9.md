# react-engine-v9 — 文字任务降轮次 + 控上下文（防 MiniMax 2013）

## Context

「2013」= MiniMax 上下文超窗（`input+output > window`，`llm_client.py:432,504-505` 已有注释与兜底 hint）。根因不是「没裁剪」——`fit_messages_to_context`、`trim_tool_results` 等多层裁剪已存在——而是**文字任务天然读多、步多、单步结果大**，轮次 × 单轮体积 把窗口在裁剪兜底之前撑爆。Linux 巡检（几十条 shell）、笔记梳理（读几十个文件）正是重灾区。

铁律贯穿：**无任何静态硬门禁**。V9 全部改动为**软提示词 + 上下文卫生**，不新增循环级阈值 / 计数器 / 硬停。

## Root Causes（五根因，见 grill 结论）

1. 提示词引导「一轮一个工具」（coach hints「调用一个工具」「二选一」`runtime.py:1017-1041`；FINAL-skip 要求「单独一轮」`runtime.py:927-929`）。
2. 工具结果原文入上下文、无压缩（READ ≤8000 `agent_tools.py:332`；shell/SEARCH ≤6000 `context_manager.py:23`）。
3. 裁剪丢「任务所需内容」→ 重读 → 更多轮（`trim_tool_results` 只留最近 24 条、每 8 轮跑 `runtime.py:1168`）。
4. 单次运行无历史上限（`history_length` 只限跨轮 `runtime.py:1464`；`max_iterations` 默认 150）。
5. token 估算偏低（`estimate_tokens ≈ len/2` `llm_client.py:298`）+ 每轮固定 `max_tokens=8192`（`runtime.py:838`）。

## Requirements

### R1 — 提示词引导「无依赖同轮批量」

系统提示 / 工具目录 / coach hints 从「一次一个工具」改为「**无依赖的独立步骤可同轮输出多个工具调用**（多个 READ/SEARCH、多个独立 SHELL 等）」，有依赖（后步需前步结果）仍分轮。引擎不改——`for step in tool_steps`（`runtime.py:1049`）已支持同轮多工具。

### R2 — 大结果落盘回显

READ/SHELL 结果超阈值（默认 4000 字符）时，**全文落盘** `task/<ts>/`（复用既有 mcp_result 落盘目录），上下文只回显「路径 + 前 N 行预览 + 总长度」。模型按需用 READ/SEARCH 取片段。SEARCH 结果已 cap N 条，不落盘。落盘判定放在 `runtime.py` push 之前（复用 `run_ts`/`save_dir`）。

### R3 — token 估算安全余量

在 `fit_messages_to_context` 基础上给 `allowed_out` 加安全余量（`estimate_tokens` 对 shell 输出/代码偏低，避免估算不足导致仍超窗）。

## Decisions

- **D1 双管齐下**：降轮次（R1 纯提示词，零引擎改动）+ 控上下文（R2/R3）。
- **D2 落盘回显**：大结果落盘 `task/<ts>/`、上下文只留「路径+预览+长度」，与 MCP 大结果机制对齐。
- **D3 无依赖同轮**：独立步骤同轮批量，有依赖仍分轮。
- **D4 阈值与范围**：落盘阈值 4000 字符；范围 READ + SHELL（超长时）；SEARCH 已 cap 不落盘。
- **D5 READ 分页留后续**：README 加 offset/limit（真正分页读大文件）是增强项，不在本 V9 硬性范围，记为 Open Question。
- **D6 铁律不变**：全部软提示词 + 上下文卫生，无静态硬门禁。

## 全链路改动

| 层 | 改动 |
|---|---|
| `system_prompt.py` | 同轮批量引导（工具目录 + coach hints 措辞）；落盘回显的「路径+预览」用法说明 |
| `runtime.py` | 工具结果落盘判定（push 前，复用 run_ts/save_dir）；coach hint 措辞从「一次一个」改「无依赖同轮」 |
| `agent_tools.py` | `_read_workplace`/shell 返回结果交给 runtime 落盘判定（本身不改截断） |
| `context_manager.py` | `push_tool_result` 接收「已落盘回显」串（或落盘在 runtime 侧完成后直接 push 预览串） |
| `llm_client.py` | `allowed_out` 安全余量 |

## Non-Goals

- 不改引擎循环本体（`_run_modular` 的同轮多工具已支持）。
- 不做 READ offset/limit（D5 留后续）。
- 不改 `fit_messages_to_context` 的裁剪策略（仅加余量）。
- 不动 MCP 既有大结果落盘机制。

## 验证要点

1. 文字任务（巡检/笔记）轮次显著下降：独立步骤同轮批量后，轮数 ≈ 依赖链长度而非步数。
2. 大 READ/SHELL 结果落盘后，上下文不再含全文，只含「路径+预览+长度」；模型能按需 READ/SEARCH 取回。
3. 长任务不再触发 MiniMax 2013（在窗口内稳定收敛）。
4. 回归：现有测试 + v6/v7/v8 需求不回归；无新增循环门禁 / 静态阈值。
