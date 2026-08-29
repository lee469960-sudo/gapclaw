# code-agent-operator-ui Specification

## Purpose

定义操作界面如何安全地启用 Code Profile，并让用户清楚区分 Standard Agent 与受控 CodeAgent 的项目、运行和工件状态。

## Requirements

### Requirement: Agent 编辑页显式配置 Code Profile

Agent 创建和编辑页面 SHALL 显示运行 Profile 选择。选择 `code` 时，界面 SHALL 要求绑定一个当前用户有访问权且已启用的 Code Project；选择 `standard` 时，界面 SHALL 不要求 Code Project 且保持既有资源配置语义。

#### Scenario: 为现有 Agent 启用 Code Profile

- **WHEN** 有项目访问权的用户编辑现有 Agent，选择 `code` 并绑定一个可用项目后保存
- **THEN** 界面保存该 Agent 的 Code Profile 与项目绑定
- **AND** 后续对话按该项目的受控 CodeAgent 流程提交任务

#### Scenario: Code Profile 缺少项目

- **WHEN** 用户选择 `code` 但未绑定项目或所选项目不可用
- **THEN** 界面阻止保存并显示可行动原因
- **AND** 不将 Agent 部分更新为 Code Profile

#### Scenario: Standard Agent 保持默认行为

- **WHEN** 用户创建或编辑 Standard Agent 且未选择 `code`
- **THEN** 界面不要求 Code Project
- **AND** Agent 保持既有 Standard 工具、资源和运行语义

### Requirement: Agent 和对话界面展示 CodeAgent 状态

系统 SHALL 在 Agent 列表、编辑页和 CodeAgent 对话页展示 Code Profile、绑定项目与当前 Manifest 就绪状态。Code run 结果 SHALL 展示稳定终态、验证证据和已封存工件的可用性，但不得将未验证结果呈现为可直接采用。

#### Scenario: 列表识别 Code Agent

- **WHEN** Agent 使用 Code Profile
- **THEN** Agent 列表显示其为 Code Agent 及绑定项目名称

#### Scenario: 显示可审阅结果

- **WHEN** Code run 产生封存工件或非成功终态
- **THEN** 对话页显示验证状态、稳定结果和可访问的审阅信息
- **AND** 只有 `patch_ready` 工件显示为可供人工审阅和接受

#### Scenario: Manifest 或项目不可用

- **WHEN** Code Agent 的项目在打开配置或提交任务时不具备有效运行条件
- **THEN** 界面显示具体门禁原因和项目管理入口
- **AND** 不伪装为普通 Agent 执行或静默降级

### Requirement: 操作界面必须支持 Claude Code runtime 启用和回滚

Agent/Profile/Manifest 相关界面 SHALL 在 Code Profile 下提供 `claude_code` runtime 的显式选择、当前启用状态和回滚路径。Standard Agent 界面 SHALL 保持既有行为；关闭 runtime feature flag 或未选择 Claude Code 时，新 CodeAgent run SHALL 回到现有 runtime。

#### Scenario: 启用 Claude Code runtime

- **WHEN** 有权用户在 CodeAgent 配置中选择 `claude_code` runtime 并保存有效配置
- **THEN** 界面显示该 Agent 或 Manifest 将使用 Claude Code coding runtime
- **AND** 后续 run 的状态展示包含 runtime 类型

#### Scenario: 回滚到现有 runtime

- **WHEN** 有权用户关闭 Claude Code runtime 或改回 legacy runtime
- **THEN** 新 run 使用现有 CodeAgent runtime
- **AND** 历史 Claude Code run 仍可查看其 runtime 证据和 artifact

### Requirement: 操作界面必须展示 Claude Code runtime 过程和能力加载

CodeAgent 对话和结果界面 SHALL 展示 Claude Code runtime 的稳定过程事件，至少包含 runtime 启动、Skill 加载、MCP 加载、工具调用、文件变化、测试执行、Verifier retry、Verifier 通过和 artifact 封存。Skill 加载事件 SHALL 展示 skill 名称、来源和版本/hash；MCP 加载事件 SHALL 展示 server 名称、授权状态和启用工具数量。失败时 SHALL 展示稳定可行动原因。

#### Scenario: 用户确认 Skill 已加载

- **WHEN** Claude Code runtime 启动并注入当前 Agent 绑定的 Skill
- **THEN** 界面展示已加载 Skill 的名称、来源和版本/hash
- **AND** 不展示 Skill 中的秘密值或未授权资源内容

#### Scenario: 用户确认 MCP 已加载

- **WHEN** Claude Code runtime 启动并注入授权 MCP
- **THEN** 界面展示 MCP server 名称、授权状态和启用工具数量
- **AND** MCP 配置失败时展示 `mcp_config_failed` 和可行动原因

#### Scenario: 用户查看自修复过程

- **WHEN** Verifier 失败后 Claude Code runtime 进入 retry
- **THEN** 界面展示 `verifier_failed_retrying`、retry 次数和脱敏失败摘要
- **AND** 不把 retry 中的未验证修改呈现为可直接采用

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
