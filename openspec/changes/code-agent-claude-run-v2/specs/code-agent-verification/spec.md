## ADDED Requirements

### Requirement: Local 发布必须提交完整结果证据供 Verifier 裁决

Verifier SHALL 检查 local 发布命令退出码、发布 ID 或等价远端证据、目标环境状态验证、命令与环境绑定、Secret 脱敏和 Git 副作用约束。缺少任一必要证据、发生状态未知或验证失败时不得标记成功。

#### Scenario: 完整证据通过

- **WHEN** 命令退出成功、发布证据与 local 状态验证均匹配任务契约
- **THEN** Verifier 允许 Run 进入成功终态
- **AND** 用户可查看脱敏证据摘要

#### Scenario: 证据不足或状态未知

- **WHEN** 发布证据缺失、目标验证失败或远端状态无法确认
- **THEN** Verifier 阻止成功交付
- **AND** Run 展示明确的非成功原因与人工处理建议
