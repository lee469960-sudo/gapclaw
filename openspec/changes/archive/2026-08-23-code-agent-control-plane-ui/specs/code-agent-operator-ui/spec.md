## Purpose

定义操作界面如何安全地启用 Code Profile，并让用户清楚区分 Standard Agent 与受控 CodeAgent 的项目、运行和工件状态。

## ADDED Requirements

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

