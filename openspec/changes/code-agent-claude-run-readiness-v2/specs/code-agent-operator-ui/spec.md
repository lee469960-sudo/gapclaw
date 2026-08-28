## ADDED Requirements

### Requirement: 操作界面必须提供 Claude Code LLM 绑定修复引导

当 Code Profile Agent 绑定的项目将使用 `coding_runtime=claude_code`，且该 Agent 的 LLM 绑定为 Group、缺少可用 API key 或缺少 model 时，Agent 编辑页、运行失败结果或项目 readiness 页面 SHALL 展示具体原因和修复入口。系统 MUST NOT 自动创建 LLMResource、自动从 Group 选择成员或自动改绑 Agent。

#### Scenario: LLM Group 显示修复入口

- **WHEN** Claude Code Agent 当前绑定 LLM Group
- **THEN** 界面展示 `llm_group_not_supported`
- **AND** 提示用户选择单个带 key 和 model 的 LLMResource
- **AND** 不自动替换当前绑定

#### Scenario: 缺少 key 或 model 显示修复入口

- **WHEN** Claude Code Agent 当前绑定的单个 LLMResource 缺少可用 key 或 model
- **THEN** 界面展示 `llm_api_key_missing` 或 `llm_model_missing`
- **AND** 提供跳转或编辑入口让用户修复配置

### Requirement: 操作界面必须拆分 Claude Code readiness 状态

CodeAgent 结果页和 run detail SHALL 分别展示 workspace 文件、Git metadata、repo root、container mount 和 model preflight 状态。界面 MUST NOT 将 repo files missing、git metadata missing、repo root mismatch、container mount failure 与 model preflight failure 统一显示为“仓库未挂载”。

#### Scenario: Git metadata 缺失

- **WHEN** Workspace 存在业务文件但 `/workspace/.git` 缺失或 Git top-level 不正确
- **THEN** 界面展示 Git metadata 或 repo root readiness 失败
- **AND** 不展示为单一的“仓库未挂载”

#### Scenario: Model preflight 失败

- **WHEN** Claude Code preflight 在 model 阶段失败
- **THEN** 界面展示 model preflight 失败及具体 reason
- **AND** 同时可展示已通过的 workspace、Skill 或 MCP 步骤
