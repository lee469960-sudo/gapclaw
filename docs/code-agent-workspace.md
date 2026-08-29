# CodeAgent Workspace 使用说明

## 开始一次 CodeAgent Run

1. 在 Agent 配置中选择 `profile=code` 的 CodeAgent，并绑定已发布的 Code Project/Manifest。
2. 打开该 Agent 的会话，发送代码任务并等待 Run 创建。
3. 左侧 **Code Workspace** 会随当前 `code_run_id` 加载仓库元数据、文件树和 Git 状态。

CodeAgent 的仓库根目录是受 Run 绑定的 `/workspace`。界面只展示当前 Run 的只读预览；`.git/**`、`.claude/**`、二进制和敏感内容不会出现在预览中。

## 查看运行过程

运行事件会直接显示在同一对话中，包括 Workspace、Skill、MCP、Claude Code 工具调用、文件变化、测试、修复、Verifier 和 Artifact 阶段。Claude Code 使用流式输出时，工具/文件/测试步骤会在执行过程中逐步出现；刷新页面或 WebSocket 重连后，客户端会从已保存的事件继续恢复，不应重复显示已完成步骤。

## 查看最终结果

终态结果卡会展示 Run 状态、摘要、Verifier 结论和 Artifact 状态。只有后端同时确认 Verifier 通过且 Sealed Artifact 可直接采用时，才会显示审阅、下载或接受 Patch 的操作；`target_not_found`、验证失败、权限错误等终态不会显示成功操作。

## `/workspace` 与 `/workplace` 的边界

- `/workspace`：CodeAgent 当前 Run 的仓库工作区，由 Manifest、Runner 和 Workspace 生命周期管理。
- `/workplace`：Standard Agent 的通用工作区，由原有 `WorkplacePanel` 管理，与 CodeAgent 仓库无关。

不要把 `/workplace/task/` 当作 CodeAgent 仓库。若 Workspace 尚未准备、已过期、挂载无效或当前服务端不支持预览，界面会显示对应状态，并保留对话结果；不会回退到通用 `/workplace` 冒充仓库内容。
