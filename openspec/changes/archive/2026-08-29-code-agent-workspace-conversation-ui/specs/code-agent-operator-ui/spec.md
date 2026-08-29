## MODIFIED Requirements

### Requirement: CodeAgent 结果页显示运行上下文与证据

CodeAgent 结果页 SHALL 将当前 Run 的 Workspace 入口、运行事件和最终结果放在同一操作上下文中，并继续显示 Skill/MCP、Verifier、失败原因和 Patch 采用条件。通用 Agent 的 `/workplace` 浏览行为保持不变。

#### Scenario: CodeAgent 结果页打开当前 Workspace

- **WHEN** 用户查看 CodeAgent Run 结果
- **THEN** 页面提供当前 Run Workspace 的预览入口
- **AND** 不把通用 `/workplace` 目录标记为该仓库

#### Scenario: 结果页显示完整过程与最终结论

- **WHEN** Run 已产生运行事件或进入终态
- **THEN** 页面在对话中显示过程事件和最终结果
- **AND** 最终结果中的可采用状态与后端 Verifier/Artifact 状态一致
