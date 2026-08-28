## Why

文字类任务（Linux 本地巡检、笔记梳理）烧轮次、爆上下文，最终触发 **MiniMax 2013（上下文超窗）**。根因已坐实（见 `docs/exploration/react-engine-v9.md`）：文字任务读多、步多、单步结果大，而提示词又引导「一轮一个工具」（coach hints「调用一个工具」「二选一」`runtime.py:1017-1041`），轮数 ≈ 步数；每轮全量重发 `cm.messages`，且 READ（≤8000）/SHELL（≤6000）结果原文 push 进历史。`fit_messages_to_context` 等裁剪已存在，但仍被撑爆。

关键事实：`_run_modular` 的 `for step in tool_steps`（`runtime.py:1049`）**已支持同轮多工具**——降轮次是纯提示词改动；真正要做的是给 READ/SHELL 补上「大结果落盘回显」通道（对齐 MCP 已有的 `mcp_result_*.json` 机制）。

## What Changes

- **R1 无依赖同轮批量引导**：提示词 / 工具目录 / coach hints 从「一次一个工具」改为「无依赖的独立步骤可同轮输出多个工具调用」；有依赖仍分轮。零引擎改动。
- **R2 大结果落盘回显**：READ/SHELL 结果超阈值（默认 4000 字符）全文落盘 `task/<ts>/`，上下文只回显「路径 + 前 N 行预览 + 总长度」；模型按需 READ/SEARCH 取回。SEARCH 已 cap 不落盘。
- **R3 token 估算安全余量**：`allowed_out` 加安全余量，弥补 `estimate_tokens` 对 shell 输出/代码的低估。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `agent-runtime`: 新增「无依赖同轮批量引导」「大结果落盘回显」「token 估算安全余量」三条需求。

## Impact

- `apps/api/app/services/agent_runtime/system_prompt.py`（R1 同轮批量引导 + R2 落盘回显用法说明）
- `apps/api/app/services/agent_runtime/runtime.py`（R1 coach hint 措辞 + R2 工具结果落盘判定）
- `apps/api/app/services/agent_runtime/context_manager.py`（R2 落盘回显串接收）
- `apps/api/app/services/llm_client.py`（R3 `allowed_out` 安全余量）
- `apps/api/tests/`（新增单测）
