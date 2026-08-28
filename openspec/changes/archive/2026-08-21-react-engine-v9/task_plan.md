# Task Plan — react-engine-v9

> **任务唯一来源**：`tasks.md`。本文件只做「执行阶段映射 + 顺序/依赖」，不新增、不修改任何需求或验收标准。需求定义以 `proposal.md`/`specs/` 为准，落地拆分以 `tasks.md` 为准。

## 阶段映射

### Phase A — R1 无依赖同轮批量引导（1.1–1.3）

纯提示词改动，零引擎改动。目标：把「一次一个工具」的引导改为「无依赖可同轮多个」。

| Task | 内容 | 落点 |
|---|---|---|
| 1.1 | 工具目录/协议说明加「无依赖独立步骤可同轮输出多个工具调用」 | `system_prompt.py` |
| 1.2 | coach hints 措辞：「调用一个工具」「二选一」「单独一轮」→「无依赖可同轮多个」 | `runtime.py` |
| 1.3 | 确认无引擎改动（`for step in tool_steps` 已支持同轮多工具） | 只读确认 |

- 依赖：1.3 是 1.1/1.2 的前置确认（先确认引擎已支持同轮，再改提示词措辞）。
- 验收对应 spec R1 三场景：同轮批量引导 / 有依赖仍分轮 / 不新增引擎机制。

### Phase B — R2 大结果落盘回显（2.1–2.4）

把 MCP 已有的「大结果落盘 + 回显路径」机制推广到 READ/SHELL。

| Task | 内容 | 落点 |
|---|---|---|
| 2.1 | 工具结果落盘判定（`push_tool_result` 前，复用 `run_ts`/`save_dir`，阈值 4000） | `runtime.py` |
| 2.2 | 支持接收「已落盘回显」串（路径 + 预览 + 长度） | `context_manager.py` |
| 2.3 | 落盘回显用法说明（按需 READ/SEARCH 取回） | `system_prompt.py` |
| 2.4 | 确认 SEARCH 不落盘（已 cap N 条） | 只读确认 |

- 依赖：2.1（落盘判定 + 回显串格式）先行；2.2 依赖 2.1 产出的回显串形态；2.3 依赖 2.1/2.2 定型的「路径+预览+长度」语义；2.4 随 2.1 一并确认。
- 验收对应 spec R2 四场景：超阈值落盘 / 未超阈值原文 / SEARCH 不落盘 / 按需取回。

### Phase C — R3 token 估算安全余量（3.1）

| Task | 内容 | 落点 |
|---|---|---|
| 3.1 | `allowed_out` 追加安全余量（`ctx - used - reserve` 基础上再扣） | `llm_client.py` |

- 独立于 A/B，可并行；余量大小需温和（design D4 风险项）。
- 验收对应 spec R3 两场景：追加安全余量 / 长任务不超窗。

### Phase D — 测试与回归（4.1–4.5）

| Task | 内容 |
|---|---|
| 4.1 | 同轮批量提示词断言（含「无依赖可同轮」，不含「一次一个」） |
| 4.2 | 落盘回显单测（超阈值落盘+预览、未超阈值原文、SEARCH 不落盘、按需取回） |
| 4.3 | `allowed_out` 安全余量单测 |
| 4.4 | 长任务（多轮 shell/READ）不再触发 2013 回归 |
| 4.5 | 全量回归绿；无新增循环门禁 / 静态阈值 |

- 依赖：4.1 需 Phase A；4.2 需 Phase B；4.3 需 Phase C；4.4/4.5 需 A+B+C 全部完成。

## 执行顺序

1. **Phase A**（先 1.3 确认，再 1.1、1.2）
2. **Phase B**（2.1 → 2.2 → 2.3，2.4 随 2.1 确认）
3. **Phase C**（3.1，可与 A/B 并行）
4. **Phase D**（4.1–4.3 单测 → 4.4 长任务回归 → 4.5 全量回归）

## 关键既有锚点（执行时以实际代码为准）

- `runtime.py`：coach hints 措辞区（design 指 `runtime.py:1017-1041`）；同轮 `for step in tool_steps`（`runtime.py:1049`）；`push_tool_result(..., clip=6000)`（`runtime.py:1154`）；`trim_tool_results`（`runtime.py:1168`）。
- `llm_client.py`：`fit_messages_to_context`（`llm_client.py:335-434`）+ `allowed_out` 封顶。
- `context_manager.py`：`push_tool_result`（`context_manager.py:24` 附近）。
- MCP 对照：`McpSessionManager` 大结果落盘 `task/<ts>/mcp_result_*.json` —— R2 对齐对象。
