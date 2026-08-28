## ADDED Requirements

### Requirement: claude_code 必须支持带 objective 的开始执行入口

当 Agent 为 Code Profile 且将使用 `coding_runtime=claude_code` 时，系统 SHALL 接受 `开始执行:<objective>` 作为显式执行入口。系统 SHALL 将冒号后的非空文本作为 Code run objective，并将原始触发文本作为审计事实保留。该入口 MUST 仅对 Code Profile + `claude_code` 生效，不得改变 Standard Agent 或 legacy CodeAgent 行为。冒号后的 objective 为空时，系统 MUST NOT 创建 Code run、准备 Workspace 或启动 runner。

#### Scenario: 带 objective 的开始执行直接创建 run

- **WHEN** Code Profile Agent 的已发布 Manifest 将启用 `claude_code`
- **AND** 用户发送 `开始执行:请修改 local 配置`
- **THEN** 系统创建 Code run，objective 为 `请修改 local 配置`
- **AND** 审计记录保留原始触发文本
- **AND** 不再要求同一任务的二次 grill 确认

#### Scenario: 空 objective 不创建 run

- **WHEN** Code Profile Agent 的已发布 Manifest 将启用 `claude_code`
- **AND** 用户发送 `开始执行:` 且冒号后无非空内容
- **THEN** 系统不创建 Code run、不准备 Workspace、不启动 runner
- **AND** 返回缺少任务目标的可行动提示

#### Scenario: legacy 和 Standard Agent 不受影响

- **WHEN** Agent 为 Standard Profile 或 Code Profile 但不会使用 `claude_code`
- **AND** 用户发送包含 `开始执行:` 的消息
- **THEN** 系统按该 Agent 现有入口语义处理
- **AND** 不因本能力启用 Claude Code run gate
