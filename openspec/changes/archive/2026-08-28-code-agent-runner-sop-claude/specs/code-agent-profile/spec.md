## ADDED Requirements

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
