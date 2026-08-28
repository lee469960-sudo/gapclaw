## Purpose

单 LLM 驱动的 ReAct 运行时,负责协议解析、工具执行、安全拦截与软性教练提示。本规格定义其在协议回复中「FINAL 与工具步骤共现」时的处置行为。

## ADDED Requirements

### Requirement: FINAL 与工具同轮共现时显式告警被跳过的工具

当单轮 LLM 回复同时包含一个 FINAL 步骤与一个或多个非 PLAN 工具步骤时,引擎 MUST 保持 FINAL 优先(被跳过的工具不执行),并 MUST 产生一条可被用户观察到的执行步骤告警与一条面向模型的软性教练提示,明确指出本轮哪些工具未执行。PLAN 步骤不属于被丢弃的工具,不触发该告警。

#### Scenario: FINAL 与 SHELL 同轮

- **WHEN** 单轮回复同时包含 `SHELL: <cmd>` 与 `FINAL: <payload>`
- **THEN** 引擎不执行 SHELL,并按 FINAL 收尾(或进入完成度复核)
- **AND** 产生一条可见步骤告警,其内容指明「本轮 FINAL 与工具同时出现,工具未执行」

#### Scenario: 仅 FINAL 无工具步骤

- **WHEN** 单轮回复仅包含 `FINAL: <payload>` 且不含任何工具步骤
- **THEN** 引擎正常收尾
- **AND** 不产生「工具被跳过」的额外告警

#### Scenario: FINAL 与 PLAN 同轮

- **WHEN** 单轮回复同时包含 `PLAN:` 与 `FINAL: <payload>`
- **THEN** PLAN 先被应用(更新任务上下文与子任务清单),随后按 FINAL 收尾
- **AND** 不触发「非 PLAN 工具被跳过」的告警
