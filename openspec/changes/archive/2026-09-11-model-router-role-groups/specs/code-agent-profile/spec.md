## ADDED Requirements

### Requirement: Claude Code 与可路由 React-Code 模型边界明确

系统 SHALL 将可路由 React-Code 角色限定为 Standard/React 运行时中的代码任务选择，不得将其解释为 Claude Code runtime 的模型来源。使用 `coding_runtime=claude_code` 的 Code Profile MUST 继续绑定单个兼容 LLMResource，并保持已冻结运行的模型可复现性。

#### Scenario: React-Code 角色不改变 Claude Code 绑定

- **WHEN** 管理者配置 React-Code 角色模型组
- **THEN** 已配置 Claude Code runtime 的 Code Profile 不得绑定该角色模型组替代其单个 LLMResource
- **AND** Claude Code 的保存前校验和运行时 fail-closed 行为保持不变

