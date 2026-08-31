## Why

当前 CodeAgent 叠加了每 run runner、发布发现、专用预检、发布证据和复杂 Manifest 运行时字段；它既没有让 Claude Code 像本地开发一样工作，也阻碍用户在已经存在的 Sandbox 中维护工具链。

## What Changes

- **BREAKING** CodeAgent 不再创建或依赖每 run 专用 runner；它复用 CodeAgent 编辑页已绑定的持久 Sandbox。
- **BREAKING** Manifest 收敛为 Git 仓库连接：来源、地址、Git 凭据和 Ref；发布时不再导入仓库、扫描源码或封存 snapshot。
- 将同一 Code Workspace 挂载给持久 Sandbox、Claude Code 与左侧文件预览；用户可从现有 Sandbox 功能进入同一环境安装工具和操作终端。
- Sandbox 未运行时仅返回可行动提示，不自动启动、不创建替代 Sandbox。
- 删除 local publish、命令发现、预检、确认、发布证据、发布专用 Verifier/Sealer、独立 runner 及其配置/事件/UI；保留普通代码修改、测试事实、diff 预览和对话过程。
- Skill、模型、MCP、策略和资源配置继续完全来自 CodeAgent 编辑页已有资源配置，不迁入 Manifest。

## Capabilities

### New Capabilities

- `code-agent-persistent-sandbox-binding`: 将 CodeAgent 已配置的持久 Sandbox 作为 Workspace 与 Claude Code 的唯一执行环境。

### Modified Capabilities

- `code-agent-control-plane`: Manifest 从完整运行契约收敛为 Git 仓库配置。
- `code-agent-sandbox-runtime`: Code 工具与 Claude Code 从每 run runner 切换为绑定的持久 Sandbox。
- `code-agent-coding-runtime`: Claude Code 在持久 Sandbox 的挂载 Workspace 中执行常规开发循环。
- `code-agent-workspace`: Workspace 生命周期与持久 Sandbox 挂载保持一致。
- `code-agent-workspace-ui`: 左侧 Workspace 明确显示并预览绑定 Sandbox 中的同一仓库目录。
- `code-agent-operator-ui`: Manifest UI 只展示 Git 连接与基线，移除运行时/发布型字段。
- `code-agent-verification`: 移除 local 发布专用验证与封存门禁，保留普通代码测试和差异事实。

## Impact

- 涉及 CodeAgent 编辑页既有 Sandbox 绑定、Manifest API/UI、Workspace 挂载、Claude Code 调度、运行结果与遗留 runner/local publish 代码删除。
- 已存在的 Manifest 运行时字段、历史 snapshot 和历史 run 保留可读；新 run 不再依赖它们。持久 Sandbox 中的 Git clone/pull、工具、权限、网络和生命周期由现有 Sandbox 功能负责。
