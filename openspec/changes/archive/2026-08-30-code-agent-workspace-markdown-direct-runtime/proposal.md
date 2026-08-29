## Why

CodeAgent 的 Workspace 文件预览目前只能显示原始文本，代码块工具栏的复制操作也没有接入事件处理；同时 Claude Code 对话仍要求用户输入特定前缀或确认词才能启动。需要把这些归档后的行为修复纳入一个可验证的 OpenSpec change，确保预览、复制和对话路由契约一致。

## What Changes

- 将 CodeAgent Workspace 文件预览按 Markdown、代码和数据文件类型进行格式化展示。
- 为预览中的代码块接入复制操作，并在剪贴板权限不可用时提供浏览器兼容回退。
- CodeAgent Claude 对话默认直接创建 Code Run；`开始执行:` 仅作为兼容旧调用的可选前缀。
- 过滤 Claude `stream-json` 的内部初始化协议事件，避免原始 JSON 出现在用户消息中。
- 在执行过程和终态结果中保留脱敏、限长的阶段代码片段与测试输出。

## Capabilities

### New Capabilities

- `code-agent-workspace-markdown-preview`: 提供 CodeAgent Workspace 文件的 Markdown/代码预览和复制交互。

### Modified Capabilities

- `code-agent-coding-runtime`: 规范 Claude stream-json 协议事件过滤及阶段片段输出。
- `code-agent-conversation-progress`: 规范 CodeAgent 对话默认直接进入 Code Runtime。

## Impact

- 前端 `CodeWorkspacePanel`、Markdown 预览工具和 AgentChat 执行过程展示。
- API CodeAgent 对话入口、Claude Runtime 事件解析和终态结果格式化。
- 相关 CodeAgent 单元测试与前端构建验证；不改变 Repository、Sandbox、Verifier 或 Artifact 的生命周期模型。
