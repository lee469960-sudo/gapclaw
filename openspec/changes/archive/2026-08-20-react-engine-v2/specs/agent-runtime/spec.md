## ADDED Requirements

### Requirement: PLAN-only 轮次注入「规划待执行」软提示

当单轮回复只包含一个 PLAN 步骤、不包含任何工具步骤（且不含 FINAL）时，引擎 MUST 注入一条软性教练提示，提醒模型「本轮已记录计划但未调用任何工具，请对第一个待办子任务输出实际的工具调用行」。该提示 MUST NOT 进行进度计数或触发硬门禁；当本轮存在被执行的工具或 FINAL 时，MUST NOT 发该提示。

#### Scenario: PLAN-only 一轮后注入提示

- **WHEN** 单轮回复只包含 `PLAN: <计划>` 且不含任何工具步骤、FINAL 或裸代码
- **THEN** 引擎注入一条软提示，内容指引模型对第一个 `[ ]` 子任务输出实际的工具调用行
- **AND** 不产生任何硬性计数或循环中断

#### Scenario: PLAN 与工具同轮不发提示

- **WHEN** 单轮回复同时包含 `PLAN:` 与至少一个工具步骤（如 `MCP:`/`SHELL:`）
- **THEN** 引擎正常执行工具，不注入「规划待执行」提示

#### Scenario: PLAN 与 FINAL 同轮不发提示

- **WHEN** 单轮回复同时包含 `PLAN:` 与 `FINAL: <payload>` 且无其它工具步骤
- **THEN** 引擎按 FINAL 收尾，不注入「规划待执行」提示
