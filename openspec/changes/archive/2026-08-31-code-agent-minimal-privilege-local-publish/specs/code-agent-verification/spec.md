## MODIFIED Requirements

### Requirement: Local 发布必须提交完整结果证据供 Verifier 裁决

Verifier SHALL 继续裁决 local 发布任务的成功。缺少必要证据、发生状态未知或验证失败时不得标记成功。Claude Code 文本或命令退出码不得单独替代 Verifier。

#### Scenario: 完整证据通过

- **WHEN** Claude Code 完成且 Verifier 匹配任务契约
- **THEN** Verifier 允许 Run 进入成功终态
- **AND** 用户可查看脱敏证据摘要

#### Scenario: 证据不足或状态未知

- **WHEN** 验证失败或远端状态无法确认
- **THEN** Verifier 阻止成功交付
- **AND** Run 展示明确的非成功原因
