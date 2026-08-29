## Why

CodeAgent 的仓库实际运行在独立的 `/workspace`，但当前界面仍以通用 Agent 的 `/workplace` 作为主要工作区视图，导致用户看不到已挂载的仓库和运行过程。对话窗口也需要同时呈现 Claude Code 的执行步骤与最终任务结果，才能让用户确认任务确实完成。

## What Changes

- 为 CodeAgent 提供绑定当前 Run 的 Workspace 浏览视图，展示仓库文件、Git 状态和变更文件。
- 保持 CodeAgent 使用隔离的 `/workspace` 运行目录；通用 Agent 的 `/workplace` 行为不变。
- 在 CodeAgent 对话框中展示 Workspace 准备、Skill/MCP 加载、工具调用、测试、修复、Verifier 和封存等过程事件。
- 在同一对话中展示最终任务状态、验证结论、变更摘要和可采用 Patch 信息。
- 对 Workspace 浏览、运行事件和最终结果实施现有 Run 权限与脱敏规则。

## Capabilities

### New Capabilities

- `code-agent-workspace-ui`: CodeAgent 专属 Workspace 文件与 Git 状态浏览。
- `code-agent-conversation-progress`: CodeAgent 运行过程事件和最终结果在对话框中的可见性。

### Modified Capabilities

- `code-agent-operator-ui`: CodeAgent 结果视图增加 Workspace 入口和过程/最终结果展示。

## Impact

- 前端：AgentChat、左侧 Workspace 面板及 CodeAgent 运行状态展示。
- 后端：CodeAgent Workspace 浏览 API、Run 事件查询/流式推送和结果序列化。
- 权限与安全：复用现有 CodeAgent Run 授权、Workspace 路径校验、Git 元数据过滤和输出脱敏。
- 不改变通用 Agent `/workplace`，不新增 Sandbox，不改变 Repository 获取与 Code Runner 隔离模型。
