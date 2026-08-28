## Why

`code-agent-v1` 已提供受控的 Code Profile、项目 Manifest 和隔离执行能力，但这些控制面对象没有可供管理员配置的 API 或页面；现有 Agent 编辑页也无法选择 `code` Profile 或绑定 Code Project。结果是能力虽已存在，却不能以安全、可理解的方式被平台用户启用。

## What Changes

- 新增 Code Project 与版本化 Manifest 的受权限保护管理 API 和 UI，支持草稿编辑、校验、发布与查看已发布版本。
- 在 Agent 创建/编辑页显式提供 Standard / Code Profile 选择；选择 Code 时只能绑定当前用户有权访问且已启用的 Code Project。
- 在 Agent 列表、编辑页和 Code 对话页展示 Profile、项目、Manifest 就绪状态与 Code run 的验证/工件结果，避免把普通 Workspace 或 Shell 权限误解为 CodeAgent 能力。
- 对未授权项目、未发布或无效 Manifest、禁用项目和不可用执行环境提供可行动的 UI/API 反馈；Standard Agent 的默认配置、权限和运行路径保持不变。

## Capabilities

### New Capabilities

- `code-agent-control-plane`: Code Project、Manifest、项目访问授权与发布状态的受控管理和可见性。
- `code-agent-operator-ui`: 在 Agent 配置和运行界面中安全启用、呈现与解释 Code Profile 及其结果。

### Modified Capabilities

- `agent-config`: Agent 配置面板支持显式 Code Profile 选择与受授权 Code Project 绑定，同时保持 Standard 默认语义。

## Impact

- 后端将增加 Code Project/Manifest 的控制面 router、请求 schema、审计/校验和页面权限配置，并复用现有 `CodeProject`、`CodeProjectManifest` 与 `can_use_code_project` 边界。
- 前端将新增 Code Project 管理路由/视图，并扩展 `Agents.vue`、Agent 卡片及 `AgentChat.vue` 的配置与状态呈现。
- 现有 Code run admission、Workspace、runner、Verifier 和 sealed artifact 机制不改变；本 change 只补齐它们的可操作控制面。
