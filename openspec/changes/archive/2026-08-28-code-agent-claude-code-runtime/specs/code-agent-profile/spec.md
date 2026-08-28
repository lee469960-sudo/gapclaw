## MODIFIED Requirements

### Requirement: Code Profile 必须显式选择且 Standard 默认兼容

系统 SHALL 仅在 Agent 或任务显式选择 `code` Profile 时启用 CodeAgent 行为；缺失 Profile 或选择 `standard` 时 SHALL 保持既有 Standard Agent 的工具、运行语义和终态。Code Profile SHALL 复用统一任务身份、权限、预算、事件和审计契约。Code Profile MAY 在已发布 Manifest 或等效冻结契约中显式选择 `coding_runtime=claude_code`；未选择时 SHALL 使用现有 CodeAgent runtime，且不得影响 Standard Agent。

#### Scenario: 未声明 Profile 的既有任务

- **WHEN** 创建或运行一个未声明 Profile 的既有任务
- **THEN** 系统按 Standard Profile 执行
- **AND** 不准备 Workspace、不暴露 Code Tools、也不产生 CodeAgent 专属终态

#### Scenario: 显式选择 Code Profile

- **WHEN** 获授权的任务显式选择 `code` Profile
- **THEN** 系统按该项目的 Code 策略准备 CodeAgent 执行上下文
- **AND** 该运行继续使用统一任务身份、预算、事件与审计记录

#### Scenario: Code Profile 选择 Claude Code runtime

- **WHEN** 获授权的 CodeAgent 项目或任务显式选择 `coding_runtime=claude_code`
- **THEN** 系统在 Code Profile 下使用 Claude Code runtime 执行编码循环
- **AND** Task、Repository、Sandbox、Manifest、Verifier、Sealer 与结果管理仍使用现有 CodeAgent 语义

#### Scenario: 关闭 Claude Code runtime

- **WHEN** 管理者关闭 `claude_code` runtime feature flag 或 Manifest 未选择该 runtime
- **THEN** 新 CodeAgent run 使用现有 runtime
- **AND** 历史 Claude Code run 与 artifact 仍可审计且不被删除
