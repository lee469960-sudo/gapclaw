## ADDED Requirements

### Requirement: 操作界面必须支持 Claude Code runtime 启用和回滚

Agent/Profile/Manifest 相关界面 SHALL 在 Code Profile 下提供 `claude_code` runtime 的显式选择、当前启用状态和回滚路径。Standard Agent 界面 SHALL 保持既有行为；关闭 runtime feature flag 或未选择 Claude Code 时，新 CodeAgent run SHALL 回到现有 runtime。

#### Scenario: 启用 Claude Code runtime

- **WHEN** 有权用户在 CodeAgent 配置中选择 `claude_code` runtime 并保存有效配置
- **THEN** 界面显示该 Agent 或 Manifest 将使用 Claude Code coding runtime
- **AND** 后续 run 的状态展示包含 runtime 类型

#### Scenario: 回滚到现有 runtime

- **WHEN** 有权用户关闭 Claude Code runtime 或改回 legacy runtime
- **THEN** 新 run 使用现有 CodeAgent runtime
- **AND** 历史 Claude Code run 仍可查看其 runtime 证据和 artifact

### Requirement: 操作界面必须展示 Claude Code runtime 过程和能力加载

CodeAgent 对话和结果界面 SHALL 展示 Claude Code runtime 的稳定过程事件，至少包含 runtime 启动、Skill 加载、MCP 加载、工具调用、文件变化、测试执行、Verifier retry、Verifier 通过和 artifact 封存。Skill 加载事件 SHALL 展示 skill 名称、来源和版本/hash；MCP 加载事件 SHALL 展示 server 名称、授权状态和启用工具数量。失败时 SHALL 展示稳定可行动原因。

#### Scenario: 用户确认 Skill 已加载

- **WHEN** Claude Code runtime 启动并注入当前 Agent 绑定的 Skill
- **THEN** 界面展示已加载 Skill 的名称、来源和版本/hash
- **AND** 不展示 Skill 中的秘密值或未授权资源内容

#### Scenario: 用户确认 MCP 已加载

- **WHEN** Claude Code runtime 启动并注入授权 MCP
- **THEN** 界面展示 MCP server 名称、授权状态和启用工具数量
- **AND** MCP 配置失败时展示 `mcp_config_failed` 和可行动原因

#### Scenario: 用户查看自修复过程

- **WHEN** Verifier 失败后 Claude Code runtime 进入 retry
- **THEN** 界面展示 `verifier_failed_retrying`、retry 次数和脱敏失败摘要
- **AND** 不把 retry 中的未验证修改呈现为可直接采用
