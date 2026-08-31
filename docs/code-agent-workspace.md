# CodeAgent Workspace 使用说明

## 开始一次 CodeAgent Run

1. 在 Code Project 的 Manifest 中只配置 Git 仓库来源、只读凭据和 ref，然后发布 Git 配置。
2. 在 Agent 编辑页选择 `profile=code`，绑定 Code Project，并绑定一个已存在的 Sandbox。
3. 手动启动该 Sandbox。若 Sandbox 停止，CodeAgent 只提示启动，不会自动创建或启动 Sandbox。
4. 打开该 Agent 的会话，直接发送代码任务。CodeAgent 会创建 Run，并在绑定 Sandbox 的持久 Workspace 中运行 Claude Code。
5. 左侧 **Code Workspace** 会随当前 `code_run_id` 加载仓库元数据、文件树、Git 状态、绑定 Sandbox 状态和容器内挂载路径。

CodeAgent 的仓库根目录位于绑定 Sandbox 的 `/workplace/code/<project_id>/workspace`。Run 启动时，平台在该 Sandbox 内执行 Git 同步；首次运行会 clone，后续运行会 fetch 并 checkout Manifest 请求的 ref。同一个 Sandbox 中的终端、Claude Code、Code Tools 和左侧 Workspace 预览都使用这一个目录。`.git/**`、`.claude/**`、二进制和敏感内容不会出现在预览中。

## 查看运行过程

运行事件会直接显示在同一对话中，包括 Workspace、Skill、MCP、Claude Code 工具调用、文件变化、测试、修复、Verifier 和 Artifact 阶段。Claude Code 使用流式输出时，工具/文件/测试步骤会在执行过程中逐步出现；刷新页面或 WebSocket 重连后，客户端会从已保存的事件继续恢复，不应重复显示已完成步骤。

## 查看最终结果

终态结果会以 Markdown 输出到对话中，展示 Run 状态、Claude Code 摘要、变更文件、测试结果和 Verifier 结论。目标文件或配置未找到时返回 `target_not_found`，不会凭空创建替代文件假装完成。Git 提交、推送和后续发布不会自动执行；如果需要提交或推送，在下一条消息中明确要求。

## Manifest、Sandbox 与 Workspace 的边界

- Manifest：只描述 Git 来源、凭据引用和请求 ref；发布时不导入仓库、不扫描源码、不封存 snapshot。
- Agent 编辑页资源配置：继续负责 Skill、MCP、模型、策略和 Sandbox 绑定。
- Sandbox：使用系统已有任意 Sandbox。平台不会为 CodeAgent 额外创建一次性 runner。
- Code Workspace：固定映射到绑定 Sandbox 的 `/workplace/code/<project_id>/workspace`。

若仓库缺少 `dbt`、`jq`、`clickhouse` 等项目工具，可以进入同一个 Sandbox 手动安装；Claude Code 也会在任务需要时优先从仓库脚本/CI 配置判断需要安装的最小依赖。若 Sandbox 权限或网络策略阻止安装，Run 会在对话结果中给出明确阻塞原因。
