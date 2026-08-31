## MODIFIED Requirements

### Requirement: 对话框必须展示 CodeAgent 运行过程

系统 SHALL 在 CodeAgent 对话中按顺序展示可用的运行事件，包括 Workspace、Skill、MCP、工具调用、测试、修复、Verifier 和封存状态。所有面向用户的阶段名称、状态、摘要、澄清和失败说明 SHALL 使用简体中文；命令、文件路径、错误码、状态标识及折叠的审计证据 MUST 保持原始值并继续遵守现有输出脱敏规则。

#### Scenario: 运行事件实时可见

- **WHEN** CodeAgent Run 产生运行事件
- **THEN** 当前对话以简体中文展示事件类型、简短状态和发生顺序
- **AND** 命令、文件路径和工具原始输出不因展示中文化而被改写
- **AND** 事件内容遵守现有输出脱敏规则

#### Scenario: 运行失败仍展示已完成步骤

- **WHEN** Run 在模型、挂载、目标定位或验证阶段失败
- **THEN** 对话保留此前已展示的 Workspace、Skill 或工具步骤
- **AND** 以简体中文明确显示失败阶段与稳定失败原因

### Requirement: 对话框必须展示最终任务结果

系统 SHALL 在 Run 进入终态后展示最终状态、Verifier 结论、变更摘要和 Patch 是否可采用，并 SHALL 与后端结果序列化保持一致。最终 Markdown 的用户可读标题、字段标签与说明 SHALL 使用简体中文；机器可读状态标识、哈希、命令和原始审计证据 MUST 保持可复制与可追溯。

#### Scenario: 成功生成 Patch

- **WHEN** Run 通过 Verifier 且生成 sealed artifact
- **THEN** 对话以简体中文显示任务完成、验证通过和 Patch 可审阅/采用

#### Scenario: 无可采用 Patch 的终态

- **WHEN** Run 以失败、`target_not_found`、`needs_user_decision` 或证据不足结束
- **THEN** 对话以简体中文显示对应终态和原因
- **AND** 不显示为任务成功或可采用 Patch
