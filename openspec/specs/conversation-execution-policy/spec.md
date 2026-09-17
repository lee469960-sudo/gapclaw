# conversation-execution-policy Specification

## Purpose

为不需要工具或外部状态的普通对话提供低延迟、可终止且可观测的执行策略，同时保留复杂任务所需的 ReAct 能力与人工闭环。

## Requirements

### Requirement: 普通对话快速路径

系统 MUST 在每个用户请求开始前识别执行模式。无显式工具、文件、代码、部署、外部查询或多步骤目标的请求 MUST 进入 `chat` 模式，并在一次有效 LLM 回复后结束；不得进入 Planner、工具发现或完成度复核。

#### Scenario: 普通问答单轮结束

- **WHEN** 用户发送普通解释、闲聊、翻译或知识问答，且未要求外部操作
- **THEN** 系统以 `chat` 模式执行一次 LLM 调用并返回有效文本
- **AND** 不执行工具调用、Planner 或完成度复核

#### Scenario: 显式工具意图进入任务模式

- **WHEN** 用户要求读取文件、查询数据、执行命令、编写代码、部署或调用指定工具
- **THEN** 系统直接进入 `task` 或 `tool` 模式
- **AND** 不使用普通对话快速路径

### Requirement: 分模式轮次与重试预算

系统 MUST 为 `chat`、`task`、`tool` 和 `human_wait` 分别执行预算。默认 `chat` 最多一次主调用和一次修复重试；修复仅限传输失败、空/截断/协议错误或明确偏题。`human_wait` MUST 暂停自动 LLM 循环。

#### Scenario: 普通对话最多一次修复

- **WHEN** `chat` 模式首次调用失败或返回空/截断/协议错误
- **THEN** 系统最多再调用一次针对性修复
- **AND** 第二次调用后无论结果如何都结束自动循环

#### Scenario: 缺少 FINAL 不触发重试

- **WHEN** `chat` 模式返回有效文本但没有 `FINAL` 标记
- **THEN** 系统直接接受该文本
- **AND** 不因缺少内部协议标记而重试

### Requirement: 无进展与重复停止

系统 MUST 记录每轮输出、工具状态、计划状态和外部状态变化。连续两轮输出指纹相同或高度相似且没有状态进展时 MUST 停止自动循环；停止原因 MUST 可观测。

#### Scenario: 重复内容硬停止

- **WHEN** 连续两轮产生相同或高度相似的内容，且没有工具结果、文件变化、计划变化或新增用户信息
- **THEN** 系统停止继续调用 LLM
- **AND** 记录 `duplicate_output_stop` 或 `no_progress_stop`

### Requirement: 不确定事项进入人工闭环

系统 MUST 仅在缺少关键信息、存在权限/高风险操作、明显歧义或一次修复后仍无法可靠完成时进入人工闭环。普通对话因重复而停止时不得自动创建人工任务，除非同时满足不确定性条件。

#### Scenario: 高风险操作等待人工

- **WHEN** 请求涉及生产部署、删除、权限变更或其他高风险动作且需要确认
- **THEN** 系统切换到 `human_wait`
- **AND** 暂停自动 LLM 调用并展示待确认原因

### Requirement: 执行策略指标可审计

每次请求 MUST 记录 `route_mode`、`llm_turn_count`、`retry_count`、停止原因和人工闭环原因（如有）；指标 MUST 脱敏且可用于比较优化前后的轮次。

#### Scenario: 普通对话产生运行指标

- **WHEN** 一次请求进入终态
- **THEN** 系统记录模式、调用轮次、重试次数和终止原因
- **AND** 记录中不包含凭据或完整敏感提示词
