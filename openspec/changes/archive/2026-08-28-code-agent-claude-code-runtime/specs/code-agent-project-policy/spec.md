## ADDED Requirements

### Requirement: Coding Runtime 策略必须冻结并保持最小权限

系统 SHALL 在 CodeAgent run 创建时冻结 `coding_runtime`、模型路径、runtime 预算、允许 Skill、允许 MCP、shell/network/filesystem 权限和停止开关状态。`claude_code` runtime 只能通过已发布 Manifest 或受控 feature flag 显式启用；所有 runtime 权限 SHALL 继承并受现有 Manifest policy、平台硬门禁和最严格预算约束。

#### Scenario: Manifest 启用 Claude Code runtime

- **WHEN** 已发布 Manifest 或冻结任务契约显式选择 `coding_runtime=claude_code` 且 feature flag 允许
- **THEN** 系统在 run 上冻结该 runtime 选择、模型配置、预算、Skill/MCP 授权和策略来源
- **AND** 后续 runtime adapter 不得扩大这些授权

#### Scenario: Runtime 请求突破策略

- **WHEN** Claude Code runtime 请求未授权路径、未授权 shell 命令、未授权网络、未授权 MCP、未绑定 Skill 或超出预算的执行
- **THEN** 系统按现有 CodeAgent 策略拒绝该动作或终止 run
- **AND** 记录稳定且不含秘密的策略拒绝原因

### Requirement: Claude Code MVP 模型路径必须以 Cloud Claude 为正式路径

MVP 阶段系统 SHALL 仅把 Cloud Claude 配置作为 `claude_code` runtime 的正式模型路径。Local Model 或 Gateway 路由 MAY 作为实验配置存在，但 MUST NOT 成为正式 CodeAgent runtime 路径，除非通过独立 benchmark gate 证明代码理解、多文件修改、tool calling、test-fix loop、长任务稳定性、错误恢复和 context management 均达标。

#### Scenario: MVP 使用 Cloud Claude

- **WHEN** `claude_code` runtime 在 MVP 正式路径中启动
- **THEN** 系统使用受管 Cloud Claude 配置或凭证注入路径
- **AND** 不要求 Local Model/Gateway 路由可用

#### Scenario: Local Model 未达准入标准

- **WHEN** Local Model 未通过规定 benchmark gate
- **THEN** 系统不得将其作为 `claude_code` runtime 的正式模型路径
- **AND** 如允许实验运行，界面和审计必须明确标记为实验能力

### Requirement: Coding Runtime 必须支持配置级回滚

系统 SHALL 支持通过 feature flag 或 Manifest runtime 选择将新 run 从 `claude_code` 回滚到现有 CodeAgent runtime。回滚 MUST NOT 删除、覆盖或改写历史 run、transcript、audit、Verifier report 或 sealed artifact。

#### Scenario: 关闭 feature flag 回滚

- **WHEN** 管理者关闭 `claude_code` runtime feature flag
- **THEN** 新 CodeAgent run 不再启动 Claude Code runtime
- **AND** 已完成或进行过的历史 Claude Code run 仍按原始事实可审计
