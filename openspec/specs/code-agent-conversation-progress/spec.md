# code-agent-conversation-progress Specification

## Purpose

让 CodeAgent 用户在同一对话中观察编码运行过程，并在任务结束时获得明确、可审计且与 Verifier 结果一致的最终任务结论。

## Requirements

### Requirement: CodeAgent 对话默认直接使用 Code Runtime

当 Agent profile 为 CodeAgent 且绑定 Claude Code runtime 时，系统 SHALL 将每条非空用户消息直接作为当前 Code Run 的任务目标提交给 Code Runtime，不得要求用户添加“开始执行:”或“开始实现”等触发词。历史前缀 MAY 作为兼容别名保留，但不得成为必需条件。

#### Scenario: 普通消息直接启动 Code Run

- **WHEN** CodeAgent 用户发送普通的非空任务描述
- **THEN** 系统创建并排队一个 CodeAgent Run
- **AND** 该消息内容作为任务目标传入 Claude Code runtime
- **AND** 对话展示正常的运行过程和终态结果

#### Scenario: 旧前缀仍可兼容

- **WHEN** 用户发送以“开始执行:”开头的任务
- **THEN** 系统可去除兼容前缀后创建 Code Run
- **AND** 不要求用户必须使用该前缀

#### Scenario: 空消息被拒绝

- **WHEN** CodeAgent 用户发送空白消息或仅包含兼容前缀的消息
- **THEN** 系统不创建 Code Run
- **AND** 返回缺少任务目标的明确提示

### Requirement: 对话框必须展示 CodeAgent 运行过程

系统 SHALL 在 CodeAgent 对话中按顺序展示可用的运行事件，包括 Workspace、Skill、MCP、工具调用、测试、修复、Verifier 和封存状态。

#### Scenario: 运行事件实时可见

- **WHEN** CodeAgent Run 产生运行事件
- **THEN** 当前对话展示事件类型、简短状态和发生顺序
- **AND** 事件内容遵守现有输出脱敏规则

#### Scenario: 运行失败仍展示已完成步骤

- **WHEN** Run 在模型、挂载、目标定位或验证阶段失败
- **THEN** 对话保留此前已展示的 Workspace、Skill 或工具步骤
- **AND** 明确显示失败阶段与稳定失败原因

### Requirement: 对话框必须展示最终任务结果

系统 SHALL 在 Run 进入终态后展示最终状态、Verifier 结论、变更摘要和 Patch 是否可采用，并 SHALL 与后端结果序列化保持一致。

#### Scenario: 成功生成 Patch

- **WHEN** Run 通过 Verifier 且生成 sealed artifact
- **THEN** 对话显示任务完成、验证通过和 Patch 可审阅/采用

#### Scenario: 无可采用 Patch 的终态

- **WHEN** Run 以失败、`target_not_found`、`needs_user_decision` 或证据不足结束
- **THEN** 对话显示对应终态和原因
- **AND** 不显示为任务成功或可采用 Patch
