## Why

模型会把「在 PLAN 子任务里写下工具名」误当成「调用工具」，而引擎只执行显式协议行（`MCP:`/`SHELL:`/…）。首轮 PLAN-only 目前零纠正（`text_only_streak` 变 1、`cur` 非空、非 dup、非裸代码，四个条件都不命中，不发任何 hint），模型就一直停在规划态，最后自述「未执行」。需要在规划态卡住的第一时间给一条定向软提示，而不是等它空转或靠通用提示兜底。

## What Changes

- 新增一条定向软提示：当单轮回复只输出 `PLAN:` 而没有调用任何工具（且无 FINAL、无被救援的裸代码）时，注入「规划待执行」教练提示，让模型对第一个 `[ ]` 子任务输出实际的工具调用行。
- 系统提示「规划·子任务」条目追加一句：子任务文本只写目标、不写工具名（含正反例），把「写计划」与「调工具」在提示层解耦。
- **不做**自动派发：引擎仍只执行显式 `MCP:`/`SHELL:` 行，PLAN 子任务只作记忆 + 回显，不从 PLAN 里解析并自动执行工具。
- 新增单测：PLAN-only 一轮后必注入「规划待执行」hint；PLAN+工具同轮则不发该 hint。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `agent-runtime`: 新增一条软性教练提示行为——PLAN-only 轮次（输出 PLAN 但未调用工具）注入「规划待执行」提示，引导执行首个待办子任务。

## Impact

- `apps/api/app/services/agent_runtime/runtime.py`（`_run_modular` 的 PLAN 分支与 `if not tool_steps:` 分支）
- `apps/api/app/services/agent_runtime/system_prompt.py`（`build_tools_desc` 的「规划·子任务」条目）
- 测试：新增 PLAN-only 提示注入时机用例；现有测试全量保持绿
