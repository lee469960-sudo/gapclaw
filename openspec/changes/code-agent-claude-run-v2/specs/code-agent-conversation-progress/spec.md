## ADDED Requirements

### Requirement: 对话必须展示 local 发布的受控过程与终态

CodeAgent 对话 SHALL 按顺序展示命令解析、preflight、dry-run、人工确认、发布执行、结果证据、验证和清理事件。展示内容 SHALL 脱敏并明确区分已执行、被拒绝、状态未知和成功状态。

#### Scenario: 用户确认后执行发布

- **WHEN** local 发布 Run 通过 preflight 并获得确认
- **THEN** 对话实时展示发布阶段、命令 ID、退出码、发布 ID 和验证摘要
- **AND** 不展示秘密值或未授权参数

#### Scenario: 发布未执行或失败

- **WHEN** preflight、确认、权限、依赖、网络、认证或验证阶段失败
- **THEN** 对话保留已完成步骤并显示稳定失败原因
- **AND** 不将 Run 显示为发布成功
