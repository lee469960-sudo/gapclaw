## MODIFIED Requirements

### Requirement: CodeAgent 必须通过显式 Coding Runtime 运行编码循环

系统 SHALL 支持 CodeAgent run 选择 `coding_runtime=claude_code`。选择 Claude Code 后，系统 SHALL 在编辑页已绑定且运行中的持久 Sandbox 的项目 Workspace 中启动 Claude Code；模型、Skill、MCP、策略和资源配置 SHALL 来自 CodeAgent 编辑页已有资源配置。系统 MUST NOT 创建独立 runner、发布流程或第二套资源配置。

#### Scenario: 默认 runtime 保持兼容
- **WHEN** CodeAgent run 未选择 `coding_runtime=claude_code`
- **THEN** 系统使用现有 CodeAgent runtime
- **AND** 不启动 Claude Code

#### Scenario: 显式选择 Claude Code runtime
- **WHEN** CodeAgent 选择 Claude Code runtime 且绑定 Sandbox 正在运行
- **THEN** Claude Code 在该 Sandbox 的挂载 Workspace 中读取、编辑、测试和执行命令
- **AND** 用户可从同一 Sandbox 终端继续该开发环境

#### Scenario: Sandbox 未运行
- **WHEN** CodeAgent 发起 Claude Code 任务而绑定 Sandbox 未运行
- **THEN** 系统返回稳定的 Sandbox 未运行提示
- **AND** 不创建临时 runner 或自动启动 Sandbox
