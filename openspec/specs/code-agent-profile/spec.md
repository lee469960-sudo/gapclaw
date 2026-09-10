# code-agent-profile Specification

## Purpose

定义 Code Profile 在统一 Agent 平台中的显式选择、最小任务契约与受限编码能力，同时确保默认 Standard Agent 的行为不发生变化。

## Requirements

### Requirement: Code Profile 必须显式选择且 Standard 默认兼容

系统 SHALL 仅在 Agent 或任务显式选择 `code` Profile 时启用 CodeAgent 行为；缺失 Profile 或选择 `standard` 时 SHALL 保持既有 Standard Agent 的工具、运行语义和终态。Code Profile SHALL 复用统一任务身份、权限、预算、事件和审计契约。Code Profile MAY 在已发布 Manifest 或等效冻结契约中显式选择 `coding_runtime=claude_code`；未选择时 SHALL 使用现有 CodeAgent runtime，且不得影响 Standard Agent。

#### Scenario: 未声明 Profile 的既有任务

- **WHEN** 创建或运行一个未声明 Profile 的既有任务
- **THEN** 系统按 Standard Profile 执行
- **AND** 不准备 Workspace、不暴露 Code Tools、也不产生 CodeAgent 专属终态

#### Scenario: 显式选择 Code Profile

- **WHEN** 获授权的任务显式选择 `code` Profile
- **THEN** 系统按该项目的 Code 策略准备 CodeAgent 执行上下文
- **AND** 该运行继续使用统一任务身份、预算、事件与审计记录

#### Scenario: Code Profile 选择 Claude Code runtime

- **WHEN** 获授权的 CodeAgent 项目或任务显式选择 `coding_runtime=claude_code`
- **THEN** 系统在 Code Profile 下使用 Claude Code runtime 执行编码循环
- **AND** Task、Repository、Sandbox、Manifest、Verifier、Sealer 与结果管理仍使用现有 CodeAgent 语义

#### Scenario: 关闭 Claude Code runtime

- **WHEN** 管理者关闭 `claude_code` runtime feature flag 或 Manifest 未选择该 runtime
- **THEN** 新 CodeAgent run 使用现有 runtime
- **AND** 历史 Claude Code run 与 artifact 仍可审计且不被删除

### Requirement: CodeAgent 任务必须具有冻结的最小契约

修改型 CodeAgent 任务 SHALL 在执行前冻结仓库、基线 commit、目标描述、允许修改范围、验证策略和预算。缺少任一必填项的任务 SHALL 不得进入写入阶段，并 SHALL 返回可行动的澄清或策略拒绝结果。

#### Scenario: 完整契约启动修改任务

- **WHEN** CodeAgent 修改任务提供了受管仓库、基线 commit、目标、允许路径、验证策略和预算
- **THEN** 系统冻结该契约并允许其进入准备阶段

#### Scenario: 缺少验收或允许范围

- **WHEN** CodeAgent 修改任务缺少验证策略或允许修改范围
- **THEN** 系统不得创建可写执行上下文
- **AND** 返回指出缺失字段的可行动结果

### Requirement: Code Profile 仅暴露受策略约束的 V1 代码能力

Code Profile SHALL 提供受项目策略约束的读取、搜索、编辑/patch、测试和受限 shell 能力。V1 SHALL 不提供自动 Git 提交、推送、创建 PR、真实秘密访问、任意网络访问、动态工具加载或 Agent 间自动委派。

#### Scenario: 允许的代码修改工具

- **WHEN** CodeAgent 调用项目策略允许的读取、搜索、编辑或测试能力
- **THEN** 系统在有效 Workspace 和能力范围内执行该调用并记录审计事件

#### Scenario: 请求 V1 非目标能力

- **WHEN** CodeAgent 请求推送代码、读取真实秘密、访问未批准网络或动态加载工具
- **THEN** 系统拒绝该动作
- **AND** 将该拒绝记录为策略事件而不执行动作

### Requirement: Claude Code 启动前必须完成开箱前 grill 确认

当 Agent 为 Code Profile 且将使用 `coding_runtime=claude_code` 时，系统 SHALL 在创建 Code run 与启动沙箱之前，使用该 Agent 绑定的 LLM 进行 grilling 对话。用户原样提交确认口令 `开始实现` 之前，系统 MUST NOT `create_code_run`，MUST NOT 准备 Workspace，MUST NOT 启动 runner。`coding_runtime=legacy` 的 Code Profile SHALL 保持现有「一条用户消息即创建 run」行为。

#### Scenario: claude_code 首条消息不起沙箱

- **WHEN** Code Profile Agent 的已发布 Manifest 将启用 `claude_code`，且用户消息不是确认口令 `开始实现`
- **THEN** 系统不创建 Code run、不启动 runner
- **AND** 使用 Agent 绑定 LLM 继续 grilling 对话

#### Scenario: 确认口令后才创建 run

- **WHEN** 同一会话中用户原样提交 `开始实现`
- **THEN** 系统创建 Code run，并将此前会话中的用户目标与 grill 上下文作为 objective
- **AND** 之后才准备 Workspace 并启动沙箱

#### Scenario: legacy 一条消息即建 run

- **WHEN** Code Profile Agent 将使用 `coding_runtime=legacy`（含 feature flag 关闭导致的降级）
- **THEN** 系统在该条用户消息上按现有行为创建 Code run
- **AND** 不要求确认口令

### Requirement: claude_code Code Agent 必须绑定单个可用 LLMResource

当 Code Profile Agent 绑定的 CodeProject 最新已发布 Manifest 使用 `coding_runtime=claude_code` 时，系统 SHALL 要求 Agent 绑定单个 LLMResource，而不是 LLM Group。该 LLMResource MUST 有可解密且非 masked 的 API key，并且 MUST 有非空 model。系统 SHALL 在 Agent 保存阶段前置拒绝不满足要求的绑定；如果历史数据绕过保存阶段，runtime MUST fail closed 为 `model_unavailable`，并提供具体 reason。

#### Scenario: LLM Group 被前置拒绝

- **WHEN** 用户保存 Code Profile Agent
- **AND** 该 Agent 绑定的项目最新已发布 Manifest 使用 `coding_runtime=claude_code`
- **AND** Agent 选择的 LLMResource `type=group`
- **THEN** 系统拒绝保存
- **AND** 返回 `llm_group_not_supported`

#### Scenario: 缺少模型凭证被前置拒绝

- **WHEN** 用户保存 Code Profile Agent
- **AND** 该 Agent 绑定的项目最新已发布 Manifest 使用 `coding_runtime=claude_code`
- **AND** Agent 选择的单个 LLMResource 缺少 API key 或 model
- **THEN** 系统拒绝保存
- **AND** 返回 `llm_api_key_missing` 或 `llm_model_missing`

#### Scenario: 历史绑定在 runtime fail closed

- **WHEN** 历史 Agent 绕过保存阶段并绑定 LLM Group 或缺少 key/model 的 LLMResource
- **AND** 用户发送 `开始实现`
- **THEN** runtime 不启动 Claude Code
- **AND** run 失败类型为 `model_unavailable`
- **AND** reason 为具体模型绑定原因

### Requirement: claude_code 必须支持带 objective 的开始执行入口

当 Agent 为 Code Profile 且将使用 `coding_runtime=claude_code` 时，系统 SHALL 接受 `开始执行:<objective>` 作为显式执行入口。系统 SHALL 将冒号后的非空文本作为 Code run objective，并将原始触发文本作为审计事实保留。该入口 MUST 仅对 Code Profile + `claude_code` 生效，不得改变 Standard Agent 或 legacy CodeAgent 行为。冒号后的 objective 为空时，系统 MUST NOT 创建 Code run、准备 Workspace 或启动 runner。

#### Scenario: 带 objective 的开始执行直接创建 run

- **WHEN** Code Profile Agent 的已发布 Manifest 将启用 `claude_code`
- **AND** 用户发送 `开始执行:请修改 local 配置`
- **THEN** 系统创建 Code run，objective 为 `请修改 local 配置`
- **AND** 审计记录保留原始触发文本
- **AND** 不再要求同一任务的二次 grill 确认

#### Scenario: 空 objective 不创建 run

- **WHEN** Code Profile Agent 的已发布 Manifest 将启用 `claude_code`
- **AND** 用户发送 `开始执行:` 且冒号后无非空内容
- **THEN** 系统不创建 Code run、不准备 Workspace、不启动 runner
- **AND** 返回缺少任务目标的可行动提示

#### Scenario: legacy 和 Standard Agent 不受影响

- **WHEN** Agent 为 Standard Profile 或 Code Profile 但不会使用 `claude_code`
- **AND** 用户发送包含 `开始执行:` 的消息
- **THEN** 系统按该 Agent 现有入口语义处理
- **AND** 不因本能力启用 Claude Code run gate

### Requirement: Claude Code 与可路由 React-Code 模型边界明确

系统 SHALL 将可路由 React-Code 角色限定为 Standard/React 运行时中的代码任务选择，不得将其解释为 Claude Code runtime 的模型来源。使用 `coding_runtime=claude_code` 的 Code Profile MUST 继续绑定单个兼容 LLMResource，并保持已冻结运行的模型可复现性。

#### Scenario: React-Code 角色不改变 Claude Code 绑定

- **WHEN** 管理者配置 React-Code 角色模型组
- **THEN** 已配置 Claude Code runtime 的 Code Profile 不得绑定该角色模型组替代其单个 LLMResource
- **AND** Claude Code 的保存前校验和运行时 fail-closed 行为保持不变
