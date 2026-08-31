## REMOVED Requirements

### Requirement: 所有 Code Tools 必须在每 run 专用容器内执行
**Reason**: CodeAgent 改为复用编辑页绑定的持久 Sandbox。
**Migration**: 将 Code Tools 路由至已绑定且运行中的 Sandbox。

### Requirement: Claude Code 必须运行在现有 run 专用 Sandbox 内
**Reason**: 不再存在每 run 专用 Sandbox。
**Migration**: 在持久 Sandbox 的项目 Workspace 中启动 Claude Code。

### Requirement: runner 容器必须在所有终态立即移除
**Reason**: 持久 Sandbox 不应随 Code run 终态删除。
**Migration**: 仅结束任务执行，不改变 Sandbox 生命周期。
